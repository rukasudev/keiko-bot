"""Where an analytics batch lands: raw events, monthly counters, guild profile.

High-volume events never become a document — they are folded into the monthly
bucket and into the guild profile, which is what the dashboards actually read.
`PROFILE_RULES` declares how each event advances the profile so no event needs
a branch of its own. Reference: docs/analytics.md
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.constants import Commands as constants
from app.data import analytics as analytics_data
from app.services import cache

TIMESTAMP = "__ts__"

PROFILE_RULES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "guild.joined": {
        "min": {"joined_at": TIMESTAMP},
        "set": {"removed_at": None},
    },
    "guild.greeting_sent": {
        "set": {"greeting_sent_at": TIMESTAMP},
    },
    "guild.removed": {
        "set": {"removed_at": TIMESTAMP},
    },
    "feature.setup_opened": {
        "inc": {"features.{feature}.setup_attempts": 1},
        "min": {"features.{feature}.setup_opened_at": TIMESTAMP},
    },
    "setup.completed": {
        "inc": {"totals.setups_completed": 1},
        "min": {"features.{feature}.completed_at": TIMESTAMP},
        "add_to_set": {"admins": "__user__"},
    },
    "setup.discarded": {
        "inc": {"totals.setups_discarded": 1},
    },
    "setup.validation_failed": {
        "inc": {"features.{feature}.validation_failures": 1},
    },
    "config.changed": {
        "inc": {"features.{feature}.config_changes": 1},
        "add_to_set": {"admins": "__user__"},
    },
    "feature.paused": {
        "set": {"features.{feature}.paused_at": TIMESTAMP},
    },
    "feature.unpaused": {
        "set": {"features.{feature}.paused_at": None},
    },
    "feature.disabled": {
        "set": {"features.{feature}.disabled_at": TIMESTAMP},
    },
    "value.delivered": {
        "inc": {"features.{feature}.value_count": 1, "totals.value_events": 1},
        "min": {"features.{feature}.first_value_at": TIMESTAMP},
        "max": {"features.{feature}.last_value_at": TIMESTAMP, "last_value_at": TIMESTAMP},
    },
    "feature.action_performed": {
        "inc": {"features.{feature}.value_count": 1, "totals.value_events": 1},
        "min": {"features.{feature}.first_value_at": TIMESTAMP},
        "max": {"features.{feature}.last_value_at": TIMESTAMP, "last_value_at": TIMESTAMP},
    },
    "value.blocked_by_permission": {
        "inc": {
            "features.{feature}.permission_failures": 1,
            "totals.permission_failures": 1,
        },
    },
    "command.failed": {
        "inc": {"totals.errors": 1},
    },
    "report.submitted": {
        "inc": {"totals.reports": 1},
    },
}


def persist(batch: List[Dict[str, Any]]) -> None:
    """A failure in any destination costs the batch, never the caller."""
    documents, increments, profile_operations, counters = _partition(batch)

    _safely(analytics_data.insert_events, documents)
    _safely(analytics_data.increment_months, increments)
    _safely(analytics_data.update_profiles, profile_operations)
    _safely(_increment_hot_window, counters)


def _partition(batch: List[Dict[str, Any]]):
    documents: List[Dict[str, Any]] = []
    increments: Dict[str, Dict[str, int]] = {}
    profile_updates: Dict[str, Dict[str, Dict[str, Any]]] = {}
    counters: Dict[str, int] = {}

    from app.services.analytics import events_catalog
    declarations = events_catalog()

    for envelope in batch:
        declaration = declarations.get(envelope["event"]) or {}
        classes = declaration.get("class") or []

        if "event" in classes:
            documents.append(envelope)

        if "counter" in classes or "event" in classes:
            _collect_increment(increments, counters, envelope)

        _collect_profile(profile_updates, envelope)

    operations = [
        analytics_data.build_profile_operation(guild_id, **parts)
        for guild_id, parts in profile_updates.items()
    ]
    return documents, increments, operations, counters


def metric_key(event: str, feature: Optional[str]) -> str:
    """Mongo update paths treat dots as nesting, so the event name loses them."""
    safe = event.replace(".", "_")
    return f"{safe}|{feature}" if feature else safe


def _collect_increment(
    increments: Dict[str, Dict[str, int]],
    counters: Dict[str, int],
    envelope: Dict[str, Any],
) -> None:
    guild_id = envelope.get("guild_id")
    if not guild_id:
        return

    ts: datetime = envelope["ts"]
    metric = metric_key(envelope["event"], envelope.get("feature"))
    bucket_id = f"{guild_id}|{ts.strftime('%Y-%m')}"

    bucket = increments.setdefault(bucket_id, {})
    field = f"days.{ts.strftime('%d')}.{metric}"
    bucket[field] = bucket.get(field, 0) + 1

    daily = constants.REDIS_ANALYTICS_DAILY.format(
        guild_id=guild_id, date=ts.strftime("%Y-%m-%d"), metric=metric
    )
    counters[daily] = counters.get(daily, 0) + 1


def _collect_profile(
    profile_updates: Dict[str, Dict[str, Dict[str, Any]]],
    envelope: Dict[str, Any],
) -> None:
    rules = PROFILE_RULES.get(envelope["event"])
    guild_id = envelope.get("guild_id")
    if not rules or not guild_id:
        return

    feature = envelope.get("feature")
    parts = profile_updates.setdefault(guild_id, {})

    for operator, fields in rules.items():
        target_key = {
            "inc": "inc",
            "set": "set_fields",
            "min": "min_fields",
            "max": "max_fields",
            "add_to_set": "add_to_set",
        }[operator]
        target = parts.setdefault(target_key, {})

        for path, value in fields.items():
            if "{feature}" in path and not feature:
                continue
            resolved_path = path.format(feature=feature) if feature else path
            resolved = _resolve_value(value, envelope)
            if operator == "inc":
                target[resolved_path] = target.get(resolved_path, 0) + resolved
            elif resolved is not None or value is None:
                target[resolved_path] = resolved

    parts.setdefault("set_fields", {})["updated_at"] = envelope["ts"]


def _resolve_value(value: Any, envelope: Dict[str, Any]) -> Any:
    if value == TIMESTAMP:
        return envelope["ts"]
    if value == "__user__":
        return envelope.get("user_id")
    return value


def _increment_hot_window(counters: Dict[str, int]) -> None:
    for key, amount in counters.items():
        cache.increment_redis_key_with_expiration(
            key, amount, constants.ANALYTICS_HOT_WINDOW_SECONDS
        )


def _safely(operation, payload) -> None:
    if not payload:
        return
    try:
        operation(payload)
    except Exception as error:
        from app import logger
        from app.constants import LogTypes as logconstants

        logger.warn(
            f"Analytics sink failed on {operation.__name__}: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


def now() -> datetime:
    return datetime.now(timezone.utc)

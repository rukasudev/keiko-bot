"""Reading side of analytics: the numbers the admin surfaces ask for.

Two kinds of read, and only the first one is cheap by construction:

- **behavior** — the guild profile and the monthly buckets, never a scan of raw
  events, so a funnel or a churn screen costs one document instead of a
  collection sweep;
- **configuration** — `config_usage` reads what guilds actually saved, through
  `cogs.feature_config_states`. That is a read per feature, and a feature with
  its own persistence pays a few reads per configured guild. It is bounded by
  the number of guilds that configured the feature, on an admin-only screen; if
  one of them grows past a few hundred, the provider is where batching goes.

Reference: docs/analytics.md
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.constants import Commands as constants
from app.constants import FormConstants as form_constants
from app.constants import ViewConstants as view_constants
from app.data import analytics as analytics_data
from app.services import analytics
from app.services import cogs as cogs_service
from app.services.analytics_sink import metric_key
from app.services.utils import ensure_list, parse_form_yaml_to_dict


def configurable_fields(feature: str) -> List[Dict[str, Any]]:
    """Every key a form can store, with the step action that produced it.

    Read straight from the YAML, so a field added to a form shows up here
    without anyone registering it anywhere.
    """
    fields: List[Dict[str, Any]] = []

    for step in parse_form_yaml_to_dict(feature):
        action = step.get("action")
        if action in form_constants.NO_ACTION_LIST or step.get("hidden"):
            continue

        defaults = step.get("defaults") or {}

        for key, node in _value_nodes(step):
            if any(field["key"] == key for field in fields):
                continue
            fields.append({
                "key": key,
                "step_key": step.get("key"),
                "action": node.get("type") or action,
                "closed_vocabulary": analytics.records_raw_choice(node),
                # What an unset field means. A setting the YAML gives a default
                # is *in effect* everywhere, whether or not a document stores
                # it, so a fill rate of zero is "nobody changed it", not
                # "nobody uses it" — a difference that decides whether the
                # setting is a candidate for removal.
                "default": _default_label(defaults.get(key)),
            })

    return fields


def _default_label(value: Any) -> Optional[str]:
    """A default worth showing: the localized ones say nothing to an operator."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and value:
        return value
    return None


def _value_nodes(step: Dict[str, Any]):
    """(key, the YAML node that declares it) for every value a step stores.

    A card lists its keys in `fields:` but declares their type and options in
    `sections:`, so the node that decides whether a value is safe to tabulate
    is not always the one that names it.
    """
    sections: Dict[str, Dict[str, Any]] = {}
    for section in step.get("sections") or []:
        if section.get("key"):
            sections[section["key"]] = section
        for state_key in (section.get("state") or {}).values():
            sections.setdefault(state_key, section)

    named = False
    for item in step.get("fields") or []:
        if isinstance(item, dict) and item.get("key"):
            named = True
            yield item["key"], sections.get(item["key"], item)

    for select in step.get("selects") or []:
        if select.get("key"):
            named = True
            yield select["key"], select

    unnamed_inputs = any(
        not isinstance(item, dict) or not item.get("key")
        for item in (step.get("fields") or [])
    )
    if (not named or unnamed_inputs) and step.get("key"):
        yield step["key"], step


def is_filled(value: Any) -> bool:
    """A saved setting counts as used only when it actually holds something."""
    if isinstance(value, dict):
        return is_filled(value.get("values", value.get("value")))
    if isinstance(value, bool):
        return True
    return value not in (None, "", [], {})


def field_value(value: Any) -> Any:
    """A saved field's value, whether or not it kept an envelope around it."""
    if isinstance(value, dict):
        return value.get("_raw_value", value.get("value", value.get("values")))
    return value


def config_usage(feature: str) -> Dict[str, Any]:
    """Which settings of a feature are actually used, across every guild.

    Needs no event: the answer is already sitting in the saved configuration, so
    it covers every guild configured before analytics existed.

    Read through `cogs.feature_config_states`, never straight from the
    collection: a feature that owns its persistence answers every raw lookup
    with `None`, and this report is read as a list of settings to delete.
    """
    documents = cogs_service.feature_config_states(feature)
    total = len(documents)
    rows = []

    for field in configurable_fields(feature):
        filled = [
            document for document in documents
            if is_filled(document.get(field["key"]))
        ]
        row = {
            **field,
            "total": total,
            "filled": len(filled),
            "share": round(100 * len(filled) / total) if total else 0,
            "values": {},
        }

        if field["closed_vocabulary"]:
            counts: Dict[str, int] = {}
            for document in filled:
                for value in ensure_list(field_value(document.get(field["key"]))):
                    label = str(value)[:constants.ANALYTICS_MAX_VALUE_LENGTH]
                    counts[label] = counts.get(label, 0) + 1
            row["values"] = dict(
                sorted(counts.items(), key=lambda item: item[1], reverse=True)
            )

        rows.append(row)

    return {"feature": feature, "guilds": total, "fields": rows}


def unused_settings(threshold: int = 10) -> List[Dict[str, Any]]:
    """Settings almost nobody fills in — candidates for removal or better copy."""
    rows = []
    for feature in constants.COMMANDS_LIST:
        usage = config_usage(feature)
        if not usage["guilds"]:
            continue
        for field in usage["fields"]:
            if field["share"] <= threshold:
                rows.append({
                    "feature": feature,
                    "key": field["key"],
                    "filled": field["filled"],
                    "total": field["total"],
                    "share": field["share"],
                    "default": field["default"],
                })
    return sorted(rows, key=lambda row: (row["share"], -row["total"]))


def days_since(moment: Optional[datetime]) -> Optional[int]:
    if not isinstance(moment, datetime):
        return None
    reference = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - reference).days


def feature_history(guild_id: str, feature: str) -> Dict[str, Any]:
    """How long a feature has been on and how much value it delivered."""
    profile = analytics_data.find_profile(guild_id) or {}
    stats = (profile.get("features") or {}).get(feature) or {}
    return {
        "days_since_enabled": days_since(stats.get("completed_at")),
        "value_events_since_enabled": stats.get("value_count") or 0,
    }


def guild_snapshot(guild_id: str) -> Dict[str, Any]:
    """The frozen profile a removal event reports, read as one document."""
    profile = analytics_data.find_profile(guild_id) or {}
    features = profile.get("features") or {}
    totals = profile.get("totals") or {}

    active = [
        key for key, stats in features.items()
        if stats.get("completed_at") and not stats.get("disabled_at")
    ]
    first_feature = min(
        (key for key in features if features[key].get("completed_at")),
        key=lambda key: features[key]["completed_at"],
        default=None,
    )

    return {
        "days_in_guild": days_since(profile.get("joined_at")) or 0,
        "features_configured": len(features),
        "features_active": len(active),
        "days_since_last_value": days_since(profile.get("last_value_at")),
        "setups_completed": totals.get("setups_completed") or 0,
        "setups_discarded": totals.get("setups_discarded") or 0,
        "value_events": totals.get("value_events") or 0,
        "permission_failures": totals.get("permission_failures") or 0,
        "distinct_admins": len(profile.get("admins") or []),
        "greeting_sent": bool(profile.get("greeting_sent_at")),
        "first_feature": first_feature,
    }


def funnel(feature: str) -> Dict[str, int]:
    """Setup opened -> completed -> first value -> value on a second day."""
    opened = analytics_data.count_events(
        {"event": "feature.setup_opened", "feature": feature}
    )
    completed = analytics_data.count_events(
        {"event": "setup.completed", "feature": feature}
    )
    discarded = analytics_data.count_events(
        {"event": "setup.discarded", "feature": feature}
    )

    activated = 0
    recurring = 0
    for profile in analytics_data.find_profiles():
        stats = (profile.get("features") or {}).get(feature) or {}
        if stats.get("first_value_at"):
            activated += 1
        if _distinct_value_days(profile.get("_id"), feature) >= 2:
            recurring += 1

    return {
        "setup_opened": opened,
        "setup_completed": completed,
        "setup_discarded": discarded,
        "activated": activated,
        "recurring": recurring,
    }


SETUP_SESSION_EVENTS = (
    "feature.setup_opened",
    "setup.step_viewed",
    "setup.validation_failed",
    "setup.required_missing",
    "setup.step_back",
    "setup.completed",
    "setup.discarded",
)


def setup_sessions(feature: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every configuration attempt, rebuilt from its events by session id.

    A session whose last event is older than the view timeout can never finish:
    the view it lived in is gone. That turns "did they abandon it" from a guess
    into a fact, and the last step it reached is where they stopped.
    """
    sessions: Dict[str, Dict[str, Any]] = {}

    for name in SETUP_SESSION_EVENTS:
        query = {"event": name}
        if feature:
            query["feature"] = feature
        for event in analytics_data.find_events(query):
            session_id = event.get("session_id")
            if not session_id:
                continue

            session = sessions.setdefault(session_id, {
                "session_id": session_id,
                "guild_id": event.get("guild_id"),
                "user_id": event.get("user_id"),
                "feature": event.get("feature"),
                "source": event.get("source"),
                "last_step": None,
                "last_step_index": -1,
                "last_seen": None,
                "validation_failures": 0,
                "required_misses": 0,
                "back_count": 0,
                "outcome": None,
                "failed_keys": [],
            })

            timestamp = event.get("ts")
            if timestamp and (not session["last_seen"] or timestamp > session["last_seen"]):
                session["last_seen"] = timestamp

            props = event.get("props") or {}
            if name == "setup.step_viewed":
                index = props.get("step_index", 0)
                if index >= session["last_step_index"]:
                    session["last_step_index"] = index
                    session["last_step"] = props.get("step_key")
            elif name == "setup.validation_failed":
                session["validation_failures"] += 1
                if props.get("error_key"):
                    session["failed_keys"].append(props["error_key"])
            elif name == "setup.required_missing":
                session["required_misses"] += 1
            elif name == "setup.step_back":
                session["back_count"] += 1
            elif name == "setup.completed":
                session["outcome"] = "completed"
            elif name == "setup.discarded":
                session["outcome"] = "discarded"
                session["last_step"] = props.get("step_key") or session["last_step"]

    for session in sessions.values():
        session["outcome"] = session["outcome"] or _expired_or_open(session["last_seen"])
        session["reason"] = _dropoff_reason(session)

    return sorted(
        sessions.values(),
        key=lambda session: session["last_seen"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _expired_or_open(last_seen: Optional[datetime]) -> str:
    """The view dies after LONG_TIMEOUT_SECONDS, so it cannot finish after that."""
    if not isinstance(last_seen, datetime):
        return "open"
    reference = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc)
    quiet_seconds = (datetime.now(timezone.utc) - reference).total_seconds()
    return "expired" if quiet_seconds > view_constants.LONG_TIMEOUT_SECONDS else "open"


def _dropoff_reason(session: Dict[str, Any]) -> Optional[str]:
    """What the recorded facts support, and nothing beyond them."""
    if session["outcome"] == "completed":
        return None
    if session["validation_failures"] >= 2:
        return "repeated-validation-failure"
    if session["required_misses"]:
        return "required-field-missing"
    if session["validation_failures"]:
        return "validation-failure"
    if session["back_count"] >= 2:
        return "navigated-back-repeatedly"
    if session["outcome"] == "discarded":
        return "discarded-without-error"
    return "left-without-a-recorded-problem"


def dropoff(feature: Optional[str] = None) -> List[Dict[str, Any]]:
    """Steps ranked by how many attempts died on them, with why."""
    unfinished = [
        session for session in setup_sessions(feature)
        if session["outcome"] in ("discarded", "expired")
    ]

    by_step: Dict[tuple, Dict[str, Any]] = {}
    for session in unfinished:
        key = (session["feature"], session["last_step"])
        row = by_step.setdefault(key, {
            "feature": key[0], "step_key": key[1],
            "lost": 0, "discarded": 0, "expired": 0, "reasons": {},
        })
        row["lost"] += 1
        row[session["outcome"]] += 1
        reason = session["reason"]
        row["reasons"][reason] = row["reasons"].get(reason, 0) + 1

    return sorted(by_step.values(), key=lambda row: row["lost"], reverse=True)


def step_durations(feature: Optional[str] = None) -> List[Dict[str, Any]]:
    """Median time on each step, slowest first.

    Measures the interval between two interactions, not attention: a long gap
    can be doubt, distraction, or the admin reading another tab. Read it to
    find candidates to look at, never as proof of confusion.
    """
    query = {"event": "setup.step_completed"}
    if feature:
        query["feature"] = feature

    samples: Dict[tuple, List[int]] = {}
    for event in analytics_data.find_events(query):
        ms = (event.get("props") or {}).get("ms_on_step")
        if not isinstance(ms, int):
            continue
        samples.setdefault((event.get("feature"), event["props"].get("step_key")), []).append(ms)

    rows = []
    for (feature_key, step_key), values in samples.items():
        values.sort()
        rows.append({
            "feature": feature_key,
            "step_key": step_key,
            "samples": len(values),
            "median_ms": values[len(values) // 2],
            "slowest_ms": values[-1],
        })

    return sorted(rows, key=lambda row: row["median_ms"], reverse=True)


def choice_distribution(feature: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """What people picked on steps whose options the YAML declares."""
    query = {"event": "setup.step_completed"}
    if feature:
        query["feature"] = feature

    counts: Dict[str, Dict[str, int]] = {}
    for event in analytics_data.find_events(query):
        props = event.get("props") or {}
        choice = props.get("choice")
        if not choice:
            continue
        key = f"{event.get('feature')}/{props.get('step_key')}"
        counts.setdefault(key, {})
        counts[key][choice] = counts[key].get(choice, 0) + 1
    return counts


def friction(feature: Optional[str] = None) -> List[Dict[str, Any]]:
    """Steps ranked by how often they reject a user, worst first."""
    query = {"event": "setup.validation_failed"}
    if feature:
        query["feature"] = feature

    by_step: Dict[tuple, Dict[str, Any]] = {}
    for event in analytics_data.find_events(query):
        props = event.get("props") or {}
        key = (event.get("feature"), props.get("step_key"), props.get("error_key"))
        row = by_step.setdefault(key, {
            "feature": key[0], "step_key": key[1], "error_key": key[2],
            "failures": 0, "sessions": set(),
        })
        row["failures"] += 1
        row["sessions"].add(event.get("session_id"))

    rows = []
    for row in by_step.values():
        sessions = len(row.pop("sessions"))
        row["sessions"] = sessions
        row["failures_per_session"] = round(row["failures"] / max(sessions, 1), 1)
        rows.append(row)

    return sorted(rows, key=lambda row: row["failures"], reverse=True)


def abandoned_features(days: int = 14) -> List[Dict[str, Any]]:
    """Configured and never delivered anything — the actionable failure."""
    rows = []
    for profile in analytics_data.find_profiles():
        for feature, stats in (profile.get("features") or {}).items():
            age = days_since(stats.get("completed_at"))
            if age is None or age < days or stats.get("first_value_at"):
                continue
            rows.append({
                "guild_id": profile.get("_id"),
                "feature": feature,
                "days_since_setup": age,
            })
    return sorted(rows, key=lambda row: row["days_since_setup"], reverse=True)


def guilds_at_risk(quiet_days: int = 14) -> List[Dict[str, Any]]:
    rows = []
    for profile in analytics_data.find_profiles():
        if profile.get("removed_at"):
            continue
        quiet = days_since(profile.get("last_value_at"))
        if quiet is None or quiet < quiet_days:
            continue
        rows.append({
            "guild_id": profile.get("_id"),
            "days_quiet": quiet,
            "value_events": (profile.get("totals") or {}).get("value_events") or 0,
        })
    return sorted(rows, key=lambda row: row["days_quiet"], reverse=True)


def session_events(session_id: str) -> List[Dict[str, Any]]:
    """Everything one configuration attempt recorded, oldest first."""
    events = analytics_data.find_events({"session_id": session_id})
    return sorted(
        events,
        key=lambda event: event.get("ts") or datetime.min.replace(tzinfo=timezone.utc),
    )


def guild_timeline(guild_id: str, limit: int = None) -> List[Dict[str, Any]]:
    return analytics_data.find_events_by_guild(
        guild_id, limit or constants.ANALYTICS_TIMELINE_READ_LIMIT
    )


def churn_comparison() -> Dict[str, Dict[str, Any]]:
    """Removed versus retained, always reported with the population size."""
    removed, retained = [], []
    for profile in analytics_data.find_profiles():
        (removed if profile.get("removed_at") else retained).append(profile)

    return {
        "removed": _population(removed),
        "retained": _population(retained),
    }


def _population(profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(profiles)
    if not total:
        return {"count": 0}

    with_value = sum(1 for profile in profiles if profile.get("last_value_at"))
    completed = sum(
        (profile.get("totals") or {}).get("setups_completed") or 0
        for profile in profiles
    )
    return {
        "count": total,
        "pct_with_value": round(100 * with_value / total),
        "avg_setups_completed": round(completed / total, 1),
    }


def _distinct_value_days(guild_id: str, feature: str) -> int:
    days = set()
    for bucket in analytics_data.find_month_buckets(guild_id):
        for day, metrics in (bucket.get("days") or {}).items():
            for event in ("value.delivered", "feature.action_performed"):
                if metrics.get(metric_key(event, feature)):
                    days.add(f"{bucket.get('month')}-{day}")
    return len(days)


def pipeline_health() -> Dict[str, int]:
    return analytics.stats()

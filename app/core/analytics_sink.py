"""Where an analytics batch lands: the raw events, and every number derived from them.

The sink is the only writer of the `analytics` database. Each server's count of every
event per day goes to `analytics.counts`; the state its events add up to goes to
`analytics.lifetime`, as `LIFETIME_RULES` declares (`when` names the props an event
must carry to advance it, and `VALUE_RULE` advances every event the catalog marks
`counts_as_value`); Keiko's own totals go to `analytics.keiko`: every event per day,
each command's calls, failures and latency, and the tallies an event declares in
`keiko_tally`. The activity and interactions documents land in the `events` database,
and the closed traces the trace sink queued in `traces.runs`.
Reference: docs/analytics.md
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from app.constants import Commands as constants
from app.core.analytics import bucket_size
from app.data.analytics import counts, keiko, lifetime
from app.data.events import activity, interactions
from app.data.traces import runs as trace_runs

TIMESTAMP = "__ts__"

LIFETIME_RULES: Dict[str, Dict[str, Any]] = {
    "guild.joined": {
        "min": {"joined_at": TIMESTAMP},
        "set": {"removed_at": None},
    },
    "guild.greeting_sent": {
        "when": {"channel_found": True},
        "set": {"greeting_sent_at": TIMESTAMP},
    },
    "guild.removed": {
        "set": {"removed_at": TIMESTAMP},
    },
    "feature.setup_opened": {
        "inc": {"features.{feature}.setup_attempts": 1},
        "min": {"features.{feature}.opened_at": TIMESTAMP},
    },
    "setup.completed": {
        "inc": {"totals.setups_completed": 1},
        "min": {"features.{feature}.completed_at": TIMESTAMP},
        "counts_user": True,
    },
    "setup.discarded": {
        "inc": {
            "totals.setups_discarded": 1,
            "features.{feature}.setups_discarded": 1,
        },
    },
    "setup.validation_failed": {
        "inc": {"features.{feature}.validation_failures": 1},
    },
    "config.changed": {
        "inc": {"features.{feature}.config_changes": 1},
        "counts_user": True,
    },
    "feature.enabled": {
        "set": {
            "features.{feature}.disabled_at": None,
            "features.{feature}.paused_at": None,
        },
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

VALUE_RULE: Dict[str, Any] = {
    "inc": {"features.{feature}.value_count": 1, "totals.value_events": 1},
    "min": {"features.{feature}.first_value_at": TIMESTAMP},
    "max": {
        "features.{feature}.last_value_at": TIMESTAMP,
        "last_value_at": TIMESTAMP,
    },
}

COMMAND_TALLIES: Dict[str, str] = {
    "command.invoked": "invoked",
    "command.failed": "failed",
}

UPDATE_OPERATORS = {
    "inc": "inc",
    "set": "set_fields",
    "min": "min_fields",
    "max": "max_fields",
}


@dataclass(frozen=True)
class Fact:
    """One emitted event, as the sink reads it."""

    type: str
    ts: datetime
    guild_id: Optional[str]
    cog: Optional[str]
    user_id: Optional[str]
    props: Mapping[str, Any]


@dataclass
class Numbers:
    """Everything one batch adds, by destination, before a single write."""

    documents: Dict[str, List[Dict[str, Any]]]
    counts: Dict[str, Dict[str, int]]
    keiko: Dict[str, Dict[str, int]]
    keiko_labels: Dict[str, Dict[str, Any]]
    lifetimes: Dict[str, Dict[str, Dict[str, Any]]]
    admins: Dict[Tuple[str, str], None]


def persist(batch: List[Dict[str, Any]]) -> None:
    """A failure in any destination costs the batch, never the caller."""
    from app.core.analytics import events_catalog

    numbers = derive(batch, events_catalog())
    moment = now()

    _safely(activity.insert_many, numbers.documents["activity"])
    _safely(interactions.insert_many, numbers.documents["interactions"])
    _safely(counts.increment, numbers.counts, moment)
    _safely(keiko.increment, numbers.keiko, moment, numbers.keiko_labels)
    _safely(lifetime.update, [
        lifetime.lifetime_operation(guild_id, **parts)
        for guild_id, parts in numbers.lifetimes.items()
    ])
    _safely(lifetime.update, [
        lifetime.admin_operation(
            guild_id, user_id, constants.ANALYTICS_LIFETIME_ADMINS_MAX
        )
        for guild_id, user_id in numbers.admins
    ])


def persist_runs(closed: Iterable[Mapping[str, Any]]) -> None:
    """The traces the trace sink queued: each command's latency, and the runs kept."""
    closed = list(closed)
    latencies: Dict[str, Dict[str, int]] = {}
    labels: Dict[str, Dict[str, Any]] = {}

    for entry in closed:
        if not entry.get("command"):
            continue
        ts: datetime = entry["ts"]
        counter_id = _month_counter("commands", ts, labels)
        _add(
            latencies.setdefault(counter_id, {}),
            f"days.{ts.strftime('%d')}.{field_name(entry['command'])}"
            f".ms.{latency_bucket(entry['duration_ms'])}",
        )

    _safely(keiko.increment, latencies, now(), labels)
    _safely(trace_runs.insert_runs, [entry["run"] for entry in closed if entry.get("run")])


def record_guild_sizes(member_counts: Dict[str, Optional[int]], moment: datetime) -> int:
    """The current size bucket of every guild that has a lifetime; never creates one."""
    operations = [
        lifetime.lifetime_operation(
            guild_id,
            set_fields={"size_bucket": bucket_size(count), "size_measured_at": moment},
            upsert=False,
        )
        for guild_id, count in member_counts.items()
        if count is not None
    ]
    lifetime.update(operations)
    return len(operations)


def derive(batch: List[Dict[str, Any]], declarations: Mapping[str, Any]) -> Numbers:
    """What one batch adds to each destination, folded so each document is one write."""
    numbers = Numbers({"activity": [], "interactions": []}, {}, {}, {}, {}, {})

    for envelope in batch:
        fact = _fact(envelope)
        declaration = declarations.get(fact.type) or {}
        collection = declaration.get("collection")
        if collection in numbers.documents:
            numbers.documents[collection].append(envelope)

        _count(numbers, fact)
        _count_keiko(numbers, fact, declaration.get("keiko_tally"))
        _advance_lifetime(numbers, fact, _lifetime_rules(fact.type, declaration))

    return numbers


def metric_key(event: str, feature: Optional[str]) -> str:
    """Mongo update paths treat dots as nesting, so the event name loses them."""
    safe = field_name(event)
    return f"{safe}|{feature}" if feature else safe


def command_metric_key(event: str, feature: Optional[str], command: str) -> str:
    """The count of one command's event: `type|cog|command`, the cog empty when none."""
    return f"{field_name(event)}|{feature or ''}|{field_name(command)}"


def field_name(text: str) -> str:
    """A value that becomes a field name, without what Mongo would read as a path."""
    return str(text).replace(".", "_").lstrip("$")


def latency_bucket(duration_ms: int) -> str:
    """The bucket a duration falls in, by the edges `Commands` declares."""
    lower = 0
    for edge in constants.COMMAND_LATENCY_EDGES_MS:
        if duration_ms < edge:
            return f"{lower}-{edge}"
        lower = edge
    return f"{lower}+"


def _fact(envelope: Mapping[str, Any]) -> Fact:
    return Fact(
        type=envelope["type"],
        ts=envelope["ts"],
        guild_id=envelope.get("guild_id"),
        cog=envelope.get("cog"),
        user_id=(envelope.get("actor") or {}).get("id"),
        props=envelope.get("props") or {},
    )


def _count(numbers: Numbers, fact: Fact) -> None:
    if not fact.guild_id:
        return

    fields = numbers.counts.setdefault(
        counts.count_id(fact.guild_id, fact.ts.strftime("%Y-%m")), {}
    )
    day = fact.ts.strftime("%d")
    _add(fields, f"days.{day}.{metric_key(fact.type, fact.cog)}")

    command = fact.props.get("command")
    if command:
        _add(fields, f"days.{day}.{command_metric_key(fact.type, fact.cog, command)}")


def _count_keiko(
    numbers: Numbers, fact: Fact, keiko_tally: Optional[Mapping[str, str]]
) -> None:
    day = fact.ts.strftime("%d")
    daily = _month_counter("day", fact.ts, numbers.keiko_labels)
    _add(
        numbers.keiko.setdefault(daily, {}),
        f"days.{day}.{metric_key(fact.type, fact.cog)}",
    )

    tally = COMMAND_TALLIES.get(fact.type)
    command = fact.props.get("command")
    if tally and command:
        monthly = _month_counter("commands", fact.ts, numbers.keiko_labels)
        fields = numbers.keiko.setdefault(monthly, {})
        prefix = f"days.{day}.{field_name(command)}"
        _add(fields, f"{prefix}.{tally}")
        error_type = fact.props.get("error_type")
        if error_type:
            _add(fields, f"{prefix}.errors.{field_name(error_type)}")

    if keiko_tally:
        name, prop = keiko_tally["name"], keiko_tally["prop"]
        _add(numbers.keiko.setdefault(f"{name}:total", {}), name)
        value = fact.props.get(prop)
        if value:
            counter_id = f"{name}:{keiko_tally['kind']}:{value}"
            _add(numbers.keiko.setdefault(counter_id, {}), name)
            numbers.keiko_labels[counter_id] = {prop: value}


def _month_counter(name: str, ts: datetime, labels: Dict[str, Dict[str, Any]]) -> str:
    month = ts.strftime("%Y-%m")
    counter_id = f"{name}:{month}"
    labels.setdefault(counter_id, {"month": month})
    return counter_id


def _lifetime_rules(
    event_type: str, declaration: Mapping[str, Any]
) -> Optional[Mapping[str, Any]]:
    if declaration.get("counts_as_value"):
        return VALUE_RULE
    return LIFETIME_RULES.get(event_type)


def _advance_lifetime(
    numbers: Numbers, fact: Fact, rules: Optional[Mapping[str, Any]]
) -> None:
    if not rules or not fact.guild_id:
        return

    required = rules.get("when", {})
    if any(fact.props.get(name) != value for name, value in required.items()):
        return

    parts = numbers.lifetimes.setdefault(fact.guild_id, {})
    for operator, target_key in UPDATE_OPERATORS.items():
        target = parts.setdefault(target_key, {})
        for path, value in (rules.get(operator) or {}).items():
            if "{feature}" in path and not fact.cog:
                continue
            resolved_path = path.format(feature=fact.cog)
            resolved = fact.ts if value == TIMESTAMP else value
            if operator == "inc":
                target[resolved_path] = target.get(resolved_path, 0) + resolved
            else:
                target[resolved_path] = resolved

    if rules.get("counts_user") and fact.user_id:
        parts["inc"].setdefault("admins_count", 0)
        numbers.admins[(fact.guild_id, fact.user_id)] = None

    parts["set_fields"]["updated_at"] = fact.ts


def _add(fields: Dict[str, int], path: str) -> None:
    fields[path] = fields.get(path, 0) + 1


def _safely(operation, payload, *arguments) -> None:
    if not payload:
        return
    try:
        operation(payload, *arguments)
    except Exception as error:
        from app import logger
        from app.constants import LogTypes as logconstants

        logger.warn(
            f"Analytics sink failed on {operation.__name__}: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


def now() -> datetime:
    return datetime.now(timezone.utc)

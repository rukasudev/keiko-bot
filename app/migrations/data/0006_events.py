"""Copy every event Keiko kept into events.changes, events.activity and
events.interactions, in the envelope analytics.record writes."""

import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.migrations.helpers import begin, insert_missing

AUDIT_COGS = (
    "block_links",
    "default_roles",
    "notifications_twitch",
    "notifications_youtube_video",
    "reminders_birthday",
    "stream_elements_commands",
    "welcome_messages",
    "settings",
)

LIFECYCLE = {
    "enabled": "feature.enabled",
    "edited": "config.changed",
    "paused": "feature.paused",
    "unpaused": "feature.unpaused",
    "disabled": "feature.disabled",
    "added": "feature.item_added",
    "removed": "feature.item_removed",
}

COPIED_FROM_AUDIT = "audit"

COLLECTIONS = {
    "command.invoked": "interactions",
    "command.failed": "interactions",
    "feature.setup_opened": "interactions",
    "feature.manager_opened": "interactions",
    "setup.step_viewed": "interactions",
    "setup.step_completed": "interactions",
    "setup.step_back": "interactions",
    "setup.validation_failed": "interactions",
    "setup.required_missing": "interactions",
    "setup.completed": "interactions",
    "feature.commit_failed": "interactions",
    "setup.discarded": "interactions",
    "setup.abandoned": "interactions",
    "setup.discard_recovered": "interactions",
    "config.changed": COPIED_FROM_AUDIT,
    "feature.enabled": COPIED_FROM_AUDIT,
    "feature.paused": COPIED_FROM_AUDIT,
    "feature.unpaused": COPIED_FROM_AUDIT,
    "feature.disabled": COPIED_FROM_AUDIT,
    "feature.item_added": COPIED_FROM_AUDIT,
    "feature.item_removed": COPIED_FROM_AUDIT,
    "value.blocked_by_permission": "activity",
    "feature.tested": "interactions",
    "guild.joined": "changes",
    "guild.removed": "changes",
    "guild.greeting_sent": "activity",
    "help.opened": "interactions",
    "dashboard.opened": "interactions",
    "report.submitted": "interactions",
}

ACTOR_KINDS = {"user": "user", "system": "bot", "job": "job"}

LINK_PROPS = (
    "channel_id",
    "link",
    "host",
    "mode",
    "reason",
    "rule",
    "match",
    "deleted",
)

BLOCK_LINKS = "block_links"

RETENTION_DAYS = 90

SAME_FAILURE_SECONDS = 5

SAME_LEAVE_SECONDS = 1

ERROR_TYPE = re.compile(r"```(\w+):")

BATCH = 500


def moment_of(row, field):
    """The row's date in `field`, or its id's creation time when the field is empty."""
    value = row.get(field)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None and isinstance(row.get("_id"), ObjectId):
        return row["_id"].generation_time
    return None


def as_text(value):
    """An id stored as a number or as text, as text; nothing for no id."""
    if value is None or value == "":
        return None
    return str(value)


def envelope(row_id, ts, event_type, guild_id, cog, actor, props, session_id=None, v=1):
    """One event in the shape analytics.record writes it."""
    return {
        "_id": row_id,
        "ts": ts,
        "type": event_type,
        "guild_id": as_text(guild_id),
        "cog": cog,
        "actor": actor,
        "trace_id": None,
        "session_id": session_id,
        "props": {name: value for name, value in props.items() if value is not None},
        "v": v,
    }


def change_from_audit(cog, row, keiko=frozenset()):
    """The events.changes document of one events.<cog> row, the bot's when one of
    `keiko` wrote it."""
    user_id = as_text(row.get("user_id"))
    return envelope(
        row["_id"],
        moment_of(row, "datetime"),
        LIFECYCLE[row["event"]],
        row["guild_id"],
        row.get("cog_key") or cog,
        {"kind": "bot" if user_id in keiko else "user", "id": user_id},
        {},
    )


def keiko_ids(audits):
    """The ids that paused two features of one server within `SAME_LEAVE_SECONDS`:
    Keiko's, which paused every feature of a server it left at once, where a person
    pauses one at a time."""
    pauses = {}
    for cog, row in audits:
        user_id, moment = as_text(row.get("user_id")), moment_of(row, "datetime")
        if row.get("event") == "paused" and user_id and moment:
            owner = (as_text(row.get("guild_id")), user_id)
            pauses.setdefault(owner, []).append((moment, row.get("cog_key") or cog))

    found = set()
    for (_guild_id, user_id), moments in pauses.items():
        moments.sort()
        for (earlier, cog), (later, other) in zip(moments, moments[1:]):
            if cog != other and (later - earlier).total_seconds() <= SAME_LEAVE_SECONDS:
                found.add(user_id)
    return frozenset(found)


def changes_from_audits(audits):
    """The events.changes documents of the (cog, row) pairs, Keiko's as the bot's."""
    keiko = keiko_ids(audits)
    return [change_from_audit(cog, row, keiko) for cog, row in audits]


def from_analytics_event(row):
    """The new document of one guild.analytics_events row: the envelope fields moved,
    `source` and `result` into the props, `event_id`, `env` and `app_version` left."""
    kind = ACTOR_KINDS[row.get("actor") or "user"]
    props = dict(row.get("props") or {})
    props["source"] = row.get("source")
    props["result"] = row.get("result")
    return envelope(
        row["_id"],
        moment_of(row, "ts"),
        row["event"],
        row.get("guild_id"),
        row.get("feature"),
        {"kind": kind, "id": as_text(row.get("user_id")) if kind == "user" else None},
        props,
        row.get("session_id"),
        row.get("v") or 1,
    )


def link_blocked(row):
    """The `link.blocked` activity of one guild.blocked_links row."""
    return envelope(
        row["_id"],
        moment_of(row, "created_at"),
        "link.blocked",
        row["guild_id"],
        BLOCK_LINKS,
        {"kind": "user", "id": as_text(row.get("user_id"))},
        {name: row.get(name) for name in LINK_PROPS},
    )


def failure_from_error(row):
    """The `command.failed` of an audit.errors row, without its traceback."""
    found = ERROR_TYPE.search(row.get("error_message") or "")
    return envelope(
        row["_id"],
        moment_of(row, "created_at"),
        "command.failed",
        None,
        row.get("command_key"),
        {"kind": "user", "id": None},
        {
            "command": row.get("command_key"),
            "error_type": found.group(1) if found else None,
        },
    )


def is_recorded(failure, recorded):
    """Whether guild.analytics_events already holds this failure: one `command.failed`
    of the same command a few seconds from it."""
    command = failure["cog"]
    return any(
        command in (event.get("feature"), (event.get("props") or {}).get("command"))
        and abs((moment_of(event, "ts") - failure["ts"]).total_seconds())
        <= SAME_FAILURE_SECONDS
        for event in recorded
    )


def audit_problem(row):
    """Why an events.<cog> row cannot be mapped, or nothing."""
    if row.get("event") not in LIFECYCLE:
        return f"unknown event {row.get('event')!r}"
    if moment_of(row, "datetime") is None:
        return "datetime is not a date"
    if as_text(row.get("guild_id")) is None:
        return "no guild_id"
    return None


def event_problem(row):
    """Why a guild.analytics_events row cannot be mapped, or nothing."""
    if row.get("event") not in COLLECTIONS:
        return f"unknown event {row.get('event')!r}"
    if (row.get("actor") or "user") not in ACTOR_KINDS:
        return f"unknown actor {row.get('actor')!r}"
    if not isinstance(row.get("ts"), datetime):
        return "ts is not a date"
    return None


def link_problem(row):
    """Why a guild.blocked_links row cannot be mapped, or nothing."""
    if moment_of(row, "created_at") is None or as_text(row.get("guild_id")) is None:
        return "no created_at or guild_id"
    return None


def error_problem(row):
    """Why an audit.errors row cannot be mapped, or nothing."""
    if moment_of(row, "created_at") is None:
        return "created_at is not a date"
    return None


def problems_in(audits, events, links, errors):
    """What this file cannot map, one line per row, named by its collection and id."""
    found = [(f"events.{cog}", row, audit_problem(row)) for cog, row in audits]
    found += [("guild.analytics_events", row, event_problem(row)) for row in events]
    found += [("guild.blocked_links", row, link_problem(row)) for row in links]
    found += [("audit.errors", row, error_problem(row)) for row in errors]
    return [
        f"{collection} {row.get('_id')}: {problem}"
        for collection, row, problem in found
        if problem
    ]


def check(db):
    """Refuse an event this file cannot map to its new collection."""
    client = db.client
    audits = [
        (cog, row)
        for cog in AUDIT_COGS
        for row in client.events[cog].find(
            {}, {"event": 1, "datetime": 1, "guild_id": 1}
        )
    ]
    events = client.guild.analytics_events.find({}, {"event": 1, "actor": 1, "ts": 1})
    links = client.guild.blocked_links.find({}, {"created_at": 1, "guild_id": 1})
    errors = client.audit.errors.find({}, {"created_at": 1})
    return problems_in(audits, list(events), list(links), list(errors))


def inserted(collection, documents):
    """Insert the documents the collection lacks, a batch at a time, and count them."""
    return sum(
        insert_missing(collection, documents[start : start + BATCH])
        for start in range(0, len(documents), BATCH)
    )


def up(db):
    """Write each event once into its collection, the oldest kept ids and dates."""
    with begin(db) as tx:
        changes = changes_from_audits(
            [
                (cog, row)
                for cog in AUDIT_COGS
                for row in tx.events[cog].find({}).sort("_id", 1)
            ]
        )
        copied = {"changes": [], "activity": [], "interactions": []}
        skipped = 0
        for row in tx.guild.analytics_events.find({}).sort("_id", 1):
            collection = COLLECTIONS[row["event"]]
            if collection == COPIED_FROM_AUDIT:
                skipped += 1
                continue
            copied[collection].append(from_analytics_event(row))

        links = [
            link_blocked(row) for row in tx.guild.blocked_links.find({}).sort("_id", 1)
        ]

        reached = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        recorded = list(tx.guild.analytics_events.find({"event": "command.failed"}))
        failures, expired, duplicated = [], 0, 0
        for row in tx.audit.errors.find({}).sort("_id", 1):
            failure = failure_from_error(row)
            if failure["ts"] < reached:
                expired += 1
            elif is_recorded(failure, recorded):
                duplicated += 1
            else:
                failures.append(failure)

        tx.count("changes_from_audit", inserted(tx.events.changes, changes))
        tx.count(
            "changes_by_keiko",
            sum(change["actor"]["kind"] == "bot" for change in changes),
        )
        tx.count(
            "changes_from_analytics", inserted(tx.events.changes, copied["changes"])
        )
        tx.count("lifecycle_events_left_to_audit", skipped)
        tx.count(
            "activity_from_analytics", inserted(tx.events.activity, copied["activity"])
        )
        tx.count("links_blocked", inserted(tx.events.activity, links))
        tx.count(
            "interactions_from_analytics",
            inserted(tx.events.interactions, copied["interactions"]),
        )
        tx.count("failures_from_errors", inserted(tx.events.interactions, failures))
        tx.count("errors_past_retention", expired)
        tx.count("errors_already_recorded", duplicated)

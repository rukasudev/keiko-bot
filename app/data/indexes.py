"""Mongo indexes this bot depends on, created once at startup."""
from typing import Any, Dict, List, Tuple

from pymongo.errors import PyMongoError

from app import mongo_client
from app.constants import Commands as constants

# (collection, keys, options) — a collection of the `guild` database, or `database.collection`.
INDEXES: List[Tuple[str, List[Tuple[str, int]], Dict[str, Any]]] = [
    (
        "blocked_links",
        [("created_at", 1)],
        {"expireAfterSeconds": constants.BLOCK_LINKS_EVENTS_TTL_SECONDS},
    ),
    ("blocked_links", [("guild_id", 1), ("created_at", -1)], {}),
    ("blocked_links", [("guild_id", 1), ("user_id", 1), ("created_at", -1)], {}),
    (
        "analytics_events",
        [("ts", 1)],
        {"expireAfterSeconds": constants.ANALYTICS_EVENTS_TTL_SECONDS},
    ),
    ("analytics_events", [("guild_id", 1), ("ts", -1)], {}),
    ("analytics_events", [("event", 1), ("ts", -1)], {}),
    ("analytics_events", [("event", 1), ("feature", 1), ("ts", -1)], {}),
    ("analytics_events", [("session_id", 1), ("ts", 1)], {}),
    (
        "analytics_guild_month",
        [("updated_at", 1)],
        {"expireAfterSeconds": constants.ANALYTICS_MONTHLY_TTL_SECONDS},
    ),
    ("analytics_guild_month", [("guild_id", 1), ("month", -1)], {}),
    (
        "analytics_guild_profile",
        [("removed_at", 1)],
        {"expireAfterSeconds": constants.ANALYTICS_PROFILE_TTL_SECONDS},
    ),
    ("analytics_guild_profile", [("last_value_at", -1)], {}),
    # debug logs: a 30-day hot window. The daily file on the Discord logs
    # channel is the archive, so the TTL here is a cost decision, not a
    # retention one.
    (
        "logs",
        [("ts", 1)],
        {"expireAfterSeconds": constants.DEBUG_LOGS_TTL_SECONDS},
    ),
    ("logs", [("level", 1), ("ts", -1)], {}),
    ("logs", [("guild_id", 1), ("ts", -1)], {}),
    ("logs", [("session_id", 1), ("ts", 1)], {}),
    ("audit.reminders", [("expires_at", 1)], {"expireAfterSeconds": 0}),
] + [
    (
        collection,
        [("guild_id", 1)],
        {"unique": True, "partialFilterExpression": {"guild_id": {"$type": "string"}}},
    )
    for collection in ["moderations"]
    + [spec["command_key"] for spec in constants.SETUP_FEATURES]
]


def located(collection: str) -> Tuple[str, str]:
    """The database and the collection an entry of INDEXES names."""
    database, dot, name = collection.partition(".")
    return (database, name) if dot else ("guild", database)


def ensure_indexes() -> None:
    """Create every index; one that cannot be created never stops the others."""
    failed = []

    for collection, keys, options in INDEXES:
        database, name = located(collection)
        try:
            mongo_client[database][name].create_index(keys, **options)
        except PyMongoError as error:
            failed.append(f"{database}.{name} {keys}: {type(error).__name__}: {error}")

    if failed:
        raise RuntimeError("; ".join(failed))

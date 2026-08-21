"""Mongo indexes this bot depends on, created once at startup."""
from typing import Any, Dict, List, Tuple

from app import mongo_client
from app.constants import Commands as constants

# (collection, keys, options) — every collection lives in the `guild` database.
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
]


def ensure_indexes() -> None:
    for collection, keys, options in INDEXES:
        mongo_client.guild[collection].create_index(keys, **options)

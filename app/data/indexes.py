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
]


def ensure_indexes() -> None:
    for collection, keys, options in INDEXES:
        mongo_client.guild[collection].create_index(keys, **options)

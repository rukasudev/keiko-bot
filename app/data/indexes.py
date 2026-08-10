"""Mongo indexes this bot depends on, created once at startup.

`create_index` is idempotent, so this runs on every boot without a migration
step. Add a row here whenever a collection needs an index or a retention
window; nothing else in the codebase should create indexes ad hoc.
"""
from typing import Any, Dict, List, Tuple

from app import mongo_client
from app.constants import Commands as constants
from app.data import blocked_links

# (database, collection, keys, options)
INDEXES: List[Tuple[str, str, List[Tuple[str, int]], Dict[str, Any]]] = [
    # Retention: blocked-link records are moderation history, not an archive.
    (
        blocked_links.DATABASE, blocked_links.COLLECTION,
        [("created_at", 1)],
        {"expireAfterSeconds": constants.BLOCK_LINKS_EVENTS_TTL_SECONDS},
    ),
    (
        blocked_links.DATABASE, blocked_links.COLLECTION,
        [("guild_id", 1), ("created_at", -1)], {},
    ),
    (
        blocked_links.DATABASE, blocked_links.COLLECTION,
        [("guild_id", 1), ("user_id", 1), ("created_at", -1)], {},
    ),
]


def ensure_indexes() -> None:
    for database, collection, keys, options in INDEXES:
        mongo_client[database][collection].create_index(keys, **options)

"""Blocked-link records: one document per link I actually deleted.

Kept apart from `events.<cog_key>` on purpose: that collection is the
configuration history behind the manager History button, and it is read with
a document shape of its own (`parse_history_data`).
"""
from typing import Any, Dict, List, Optional

from app import mongo_client
from app.data.util import parse_insert_timestamp

DATABASE = "moderations"
COLLECTION = "blocked_links"


def _collection():
    return mongo_client[DATABASE][COLLECTION]


def _newest_first(record: Dict[str, Any]) -> float:
    created_at = record.get("created_at")
    return created_at.timestamp() if hasattr(created_at, "timestamp") else 0.0


def insert_blocked_link(data: Dict[str, Any]):
    return _collection().insert_one(parse_insert_timestamp(dict(data)))


def find_blocked_links_by_guild(
    guild_id: str, user_id: Optional[str] = None, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    query = {"guild_id": str(guild_id)}
    if user_id:
        query["user_id"] = str(user_id)

    # .sort() tells the real driver to use the index; the ordering is redone
    # here because the offline mock cursor ignores it.
    records = list(_collection().find(query, {"_id": False}).sort("created_at", -1))
    records.sort(key=_newest_first, reverse=True)
    return records[:limit] if limit else records


def count_blocked_links_by_guild(guild_id: str) -> int:
    return _collection().count_documents({"guild_id": str(guild_id)})


def delete_blocked_links_by_guild(guild_id: str):
    return _collection().delete_many({"guild_id": str(guild_id)})

from typing import Any, Dict, List, Optional

from app import mongo_client
from app.data.util import parse_insert_timestamp


def _newest_first(record: Dict[str, Any]) -> float:
    created_at = record.get("created_at")
    return created_at.timestamp() if hasattr(created_at, "timestamp") else 0.0


def insert_blocked_link(data: Dict[str, Any]):
    return mongo_client.guild.blocked_links.insert_one(parse_insert_timestamp(dict(data)))


def find_blocked_links_by_guild(
    guild_id: str, user_id: Optional[str] = None, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    query = {"guild_id": str(guild_id)}
    if user_id:
        query["user_id"] = str(user_id)

    records = list(
        mongo_client.guild.blocked_links.find(query, {"_id": False}).sort("created_at", -1)
    )
    records.sort(key=_newest_first, reverse=True)
    return records[:limit] if limit else records


def count_blocked_links_by_guild(guild_id: str) -> int:
    return mongo_client.guild.blocked_links.count_documents({"guild_id": str(guild_id)})


def delete_blocked_links_by_guild(guild_id: str):
    return mongo_client.guild.blocked_links.delete_many({"guild_id": str(guild_id)})

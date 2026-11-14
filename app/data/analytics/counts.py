"""`analytics.counts`: one document per server and month, each day's count of every event."""
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from pymongo import UpdateOne

from app import mongo_client


def count_id(guild_id: str, month: str) -> str:
    """The `_id` of a server's counts for a month (`YYYY-MM`)."""
    return f"{guild_id}|{month}"


def increment(increments: Mapping[str, Mapping[str, int]], moment: datetime) -> Any:
    """Add `{count_id: {"days.dd.metric": amount}}` in one round trip."""
    if not increments:
        return None

    operations = []
    for document_id, fields in increments.items():
        guild_id, month = document_id.split("|", 1)
        operations.append(UpdateOne(
            {"_id": document_id},
            {
                "$inc": dict(fields),
                "$set": {"updated_at": moment},
                "$setOnInsert": {"guild_id": guild_id, "month": month},
            },
            upsert=True,
        ))
    return mongo_client.analytics.counts.bulk_write(operations, ordered=False)


def find_by_guild(
    guild_id: str, since_month: Optional[str] = None
) -> List[Dict[str, Any]]:
    """A server's monthly counts, newest month first, from `since_month` on when given."""
    query: Dict[str, Any] = {"guild_id": str(guild_id)}
    if since_month:
        query["month"] = {"$gte": since_month}
    return list(mongo_client.analytics.counts.find(query).sort("month", -1))


def find_by_month(month: str) -> List[Dict[str, Any]]:
    """Every server's counts for one month."""
    return list(mongo_client.analytics.counts.find({"month": month}))


def delete_by_guild(guild_id: str) -> int:
    """Delete every month of a server's counts."""
    return mongo_client.analytics.counts.delete_many(
        {"guild_id": str(guild_id)}
    ).deleted_count

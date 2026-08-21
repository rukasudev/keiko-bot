from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import UpdateOne

from app import mongo_client


def insert_events(documents: List[Dict[str, Any]]):
    """Raw low-volume events. Unordered so one bad document keeps the rest."""
    if not documents:
        return None
    return mongo_client.guild.analytics_events.insert_many(documents, ordered=False)


def increment_months(increments: Dict[str, Dict[str, int]]):
    """`{bucket_id: {dotted_field: amount}}` folded into one round trip."""
    if not increments:
        return None

    now = datetime.now(timezone.utc)
    operations = []
    for bucket_id, fields in increments.items():
        guild_id, month = bucket_id.split("|", 1)
        operations.append(UpdateOne(
            {"_id": bucket_id},
            {
                "$inc": fields,
                "$set": {"updated_at": now},
                "$setOnInsert": {"guild_id": guild_id, "month": month},
            },
            upsert=True,
        ))
    return mongo_client.guild.analytics_guild_month.bulk_write(operations, ordered=False)


def update_profiles(operations: List[UpdateOne]):
    if not operations:
        return None
    return mongo_client.guild.analytics_guild_profile.bulk_write(operations, ordered=False)


def build_profile_operation(
    guild_id: str,
    inc: Optional[Dict[str, int]] = None,
    set_fields: Optional[Dict[str, Any]] = None,
    set_on_insert: Optional[Dict[str, Any]] = None,
    min_fields: Optional[Dict[str, Any]] = None,
    max_fields: Optional[Dict[str, Any]] = None,
    add_to_set: Optional[Dict[str, Any]] = None,
) -> UpdateOne:
    update: Dict[str, Any] = {}
    if inc:
        update["$inc"] = inc
    if set_fields:
        update["$set"] = set_fields
    if set_on_insert:
        update["$setOnInsert"] = set_on_insert
    if min_fields:
        update["$min"] = min_fields
    if max_fields:
        update["$max"] = max_fields
    if add_to_set:
        update["$addToSet"] = {
            key: value for key, value in add_to_set.items() if value is not None
        }
    return UpdateOne({"_id": str(guild_id)}, update, upsert=True)


def find_profile(guild_id: str) -> Optional[Dict[str, Any]]:
    return mongo_client.guild.analytics_guild_profile.find_one({"_id": str(guild_id)})


def find_profiles(query: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    return list(mongo_client.guild.analytics_guild_profile.find(query or {}))


def find_events_by_guild(guild_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    records = list(
        mongo_client.guild.analytics_events
        .find({"guild_id": str(guild_id)}, {"_id": False})
        .sort("ts", -1)
    )
    records.sort(key=_newest_first, reverse=True)
    return records[:limit] if limit else records


def count_events(query: Dict[str, Any]) -> int:
    return mongo_client.guild.analytics_events.count_documents(query)


def find_events(query: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(mongo_client.guild.analytics_events.find(query, {"_id": False}))


def find_month_buckets(guild_id: str) -> List[Dict[str, Any]]:
    return list(mongo_client.guild.analytics_guild_month.find({"guild_id": str(guild_id)}))


def find_month_buckets_by_month(month: str) -> List[Dict[str, Any]]:
    return list(mongo_client.guild.analytics_guild_month.find({"month": month}))


def delete_analytics_by_guild(guild_id: str) -> Dict[str, int]:
    guild_id = str(guild_id)
    return {
        "events": mongo_client.guild.analytics_events.delete_many(
            {"guild_id": guild_id}
        ).deleted_count,
        "months": mongo_client.guild.analytics_guild_month.delete_many(
            {"guild_id": guild_id}
        ).deleted_count,
        "profile": mongo_client.guild.analytics_guild_profile.delete_many(
            {"_id": guild_id}
        ).deleted_count,
    }


def _newest_first(record: Dict[str, Any]) -> float:
    ts = record.get("ts")
    return ts.timestamp() if hasattr(ts, "timestamp") else 0.0

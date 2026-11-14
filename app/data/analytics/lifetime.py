"""`analytics.lifetime`: one document per server, the state its events add up to."""
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pymongo import UpdateOne

from app import mongo_client


def lifetime_operation(
    guild_id: str,
    *,
    inc: Optional[Mapping[str, int]] = None,
    set_fields: Optional[Mapping[str, Any]] = None,
    min_fields: Optional[Mapping[str, Any]] = None,
    max_fields: Optional[Mapping[str, Any]] = None,
    upsert: bool = True,
) -> UpdateOne:
    """One server's update, built from the operators that carry something."""
    update: Dict[str, Any] = {}
    for operator, fields in (
        ("$inc", inc),
        ("$set", set_fields),
        ("$min", min_fields),
        ("$max", max_fields),
    ):
        if fields:
            update[operator] = dict(fields)
    return UpdateOne({"_id": str(guild_id)}, update, upsert=upsert)


def admin_operation(guild_id: str, user_id: str, cap: int) -> UpdateOne:
    """Add a member to a server's admins once, while the server has fewer than `cap`."""
    return UpdateOne(
        {
            "_id": str(guild_id),
            "admins": {"$ne": str(user_id)},
            "admins_count": {"$lt": cap},
        },
        {"$addToSet": {"admins": str(user_id)}, "$inc": {"admins_count": 1}},
    )


def update(operations: Sequence[UpdateOne]) -> Any:
    """Apply the servers' updates in one round trip."""
    if not operations:
        return None
    return mongo_client.analytics.lifetime.bulk_write(list(operations), ordered=False)


def find_lifetime(guild_id: str) -> Optional[Dict[str, Any]]:
    """One server's lifetime."""
    return mongo_client.analytics.lifetime.find_one({"_id": str(guild_id)})


def find_lifetimes() -> List[Dict[str, Any]]:
    """Every server's lifetime."""
    return list(mongo_client.analytics.lifetime.find({}))


def find_quiet(before: datetime) -> List[Dict[str, Any]]:
    """The servers Keiko is in whose last value came before `before`, or never came."""
    quiet = mongo_client.analytics.lifetime.find(
        {"removed_at": None, "last_value_at": {"$lt": before}}
    )
    never = mongo_client.analytics.lifetime.find(
        {"removed_at": None, "last_value_at": None}
    )
    return [*quiet, *never]


def delete_by_guild(guild_id: str) -> int:
    """Delete a server's lifetime."""
    return mongo_client.analytics.lifetime.delete_many(
        {"_id": str(guild_id)}
    ).deleted_count

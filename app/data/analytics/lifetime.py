"""analytics.lifetime: one document per server, the state its events add up to."""

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pymongo import UpdateOne

from app.data.collection import Document, Repository


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


class Lifetime(Repository):
    """Every server's lifetime."""

    async def update(self, operations: Sequence[UpdateOne]) -> Any:
        """Apply the servers' updates in one round trip."""
        if not operations:
            return None
        return await self._collection.bulk_write(list(operations), ordered=False)

    async def find_lifetime(self, guild_id: Any) -> Optional[Document]:
        """One server's lifetime."""
        return await self._collection.find_one({"_id": str(guild_id)})

    async def find_lifetimes(self) -> List[Document]:
        """Every server's lifetime."""
        return await self._collection.find({}).to_list()

    async def find_quiet(self, before: datetime) -> List[Document]:
        """The servers Keiko is in whose last value came before `before`, or never
        came."""
        quiet = self._collection.find(
            {"removed_at": None, "last_value_at": {"$lt": before}}
        )
        never = self._collection.find({"removed_at": None, "last_value_at": None})
        return [*await quiet.to_list(), *await never.to_list()]

    async def delete_by_guild(self, guild_id: Any) -> int:
        """Delete a server's lifetime."""
        result = await self._collection.delete_many({"_id": str(guild_id)})
        return result.deleted_count

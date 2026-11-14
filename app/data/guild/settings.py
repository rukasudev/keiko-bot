"""guild.settings: one document per server, its language, time zone and prefix in
`options`, its owner and Keiko's presence in `state`, read as the flat server
document."""

from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Sequence, Set

from app.data.collection import Document, Repository


class Settings(Repository):
    """Every server's settings."""

    async def find_settings(self, guild_id: Any) -> Optional[Document]:
        """The server document: `guild_id`, the options at the top, its owner, Keiko's
        presence and the stamps."""
        stored = await self._collection.find_one({"_id": str(guild_id)})
        return None if stored is None else _flat(stored)

    async def find_guild_ids_with_settings(self, guild_ids: Sequence[Any]) -> Set[str]:
        """The ids among `guild_ids` that have a server document."""
        found = self._collection.find(
            {"_id": {"$in": [str(guild_id) for guild_id in guild_ids]}}, {"_id": True}
        )
        return {str(document["_id"]) async for document in found}

    async def record_join(
        self, guild_id: Any, owner_id: str, options: Mapping[str, Any]
    ) -> Any:
        """Keiko is in the server from now, owned by `owner_id`; `options` only where
        the server has none saved."""
        now = datetime.now(tz=timezone.utc)
        return await self._collection.update_one(
            {"_id": str(guild_id)},
            {
                "$set": {
                    "state.owner_id": str(owner_id),
                    "state.present": True,
                    "state.joined_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"options": dict(options), "created_at": now},
            },
            upsert=True,
        )

    async def record_leave(self, guild_id: Any) -> Any:
        """Keiko left the server now."""
        now = datetime.now(tz=timezone.utc)
        return await self._collection.update_one(
            {"_id": str(guild_id)},
            {"$set": {"state.present": False, "state.left_at": now, "updated_at": now}},
        )

    async def set_option(self, guild_id: Any, key: str, value: Any) -> Any:
        """Save one of the server's options."""
        return await self._collection.update_one(
            {"_id": str(guild_id)},
            {
                "$set": {
                    f"options.{key}": value,
                    "updated_at": datetime.now(tz=timezone.utc),
                }
            },
        )

    async def count_servers_by_owner(self, owner_id: str) -> int:
        """How many servers `owner_id` owns."""
        return await self._collection.count_documents({"state.owner_id": str(owner_id)})

    async def find_servers_by_owner(self, owner_id: str) -> List[Document]:
        """The server documents of the servers `owner_id` owns."""
        found = self._collection.find({"state.owner_id": str(owner_id)})
        return [_flat(stored) async for stored in found]

    async def count_absent(self, guild_ids: Sequence[Any]) -> int:
        """How many of `guild_ids` Keiko is no longer in."""
        return await self._collection.count_documents(
            {
                "_id": {"$in": [str(guild_id) for guild_id in guild_ids]},
                "state.present": False,
            }
        )

    async def aggregate_settings(
        self, pipeline: Sequence[Mapping[str, Any]]
    ) -> List[Document]:
        """The documents an aggregation `pipeline` over every server returns."""
        rows = await self._collection.aggregate(list(pipeline))
        return await rows.to_list()


def _flat(stored: Mapping[str, Any]) -> Document:
    state = stored.get("state") or {}
    document: Document = {"guild_id": str(stored["_id"])}
    document.update(stored.get("options") or {})
    for field in ("owner_id", "present", "joined_at", "left_at"):
        document[field] = state.get(field)
    for stamp in ("created_at", "updated_at"):
        document[stamp] = stored.get(stamp)
    return document

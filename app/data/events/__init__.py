"""The `events` database: what happened, one envelope in three collections.

`changes` is someone changing something, `activity` a command acting, and
`interactions` someone asking for something. The filters, the grouping and the reads
the three share live here; each module adds what only its collection does.
"""

from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence, Tuple

from app.data.collection import Cursor, Document, Repository, Rows


def selection(
    guild_id: Any = None,
    cog: Optional[str] = None,
    types: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    actor_id: Any = None,
) -> Dict[str, Any]:
    """The filter of the events that match every value given, in index order."""
    query: Dict[str, Any] = {}
    if guild_id is not None:
        query["guild_id"] = str(guild_id)
    if cog is not None:
        query["cog"] = cog
    if actor_id is not None:
        query["actor.id"] = str(actor_id)
    if types is not None:
        query["type"] = {"$in": list(types)}
    if since is not None:
        query["ts"] = {"$gte": since}
    return query


def newest_first(cursor: Cursor, limit: Optional[int]) -> Cursor:
    """The cursor's events newest first, at most `limit` of them read."""
    cursor = cursor.sort("ts", -1)
    if limit:
        cursor = cursor.limit(limit)
    return cursor


def grouped(
    query: Dict[str, Any], field: str, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """The pipeline counting the matching events by the value at `field`, most first."""
    pipeline: List[Dict[str, Any]] = [
        {"$match": query},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
    ]
    if limit:
        pipeline.append({"$limit": limit})
    return pipeline


async def counted(rows: Rows) -> List[Tuple[Any, int]]:
    """The rows of a grouping as `(value, count)` pairs, in their order."""
    return [(row["_id"], row["count"]) for row in await rows.to_list()]


class EventCollection(Repository):
    """What every event collection reads and erases the same way."""

    async def find_by_guild(
        self,
        guild_id: Any,
        cog: Optional[str] = None,
        types: Optional[Sequence[str]] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
        actor_id: Any = None,
    ) -> List[Document]:
        """A server's events, or one actor's in it, newest first, at most `limit`."""
        query = selection(guild_id, cog, types, since, actor_id)
        found = self._collection.find(query, {"_id": False})
        return await newest_first(found, limit).to_list()

    async def delete_by_guild(self, guild_id: Any, cog: Optional[str] = None) -> int:
        """Erase a server's events, or one cog's, and count them."""
        result = await self._collection.delete_many(selection(guild_id, cog))
        return result.deleted_count


class BatchedEvents(EventCollection):
    """An event collection the analytics sink writes in batches and the monthly
    archive streams."""

    async def insert_many(self, documents: List[Document]) -> Any:
        """Write a batch. Unordered so one bad document keeps the rest."""
        return await self._collection.insert_many(documents, ordered=False)

    async def iter_between(
        self, start: datetime, end: datetime
    ) -> AsyncGenerator[Document, None]:
        """Every event from `start` up to `end`, oldest first, streamed rather than
        held."""
        found = self._collection.find({"ts": {"$gte": start, "$lt": end}})
        async for document in found.sort("ts", 1):
            yield document

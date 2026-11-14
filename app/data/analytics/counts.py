"""analytics.counts: one document per server and month, each day's count of every
event."""

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from pymongo import UpdateOne

from app.data.collection import Document, Repository


def count_id(guild_id: str, month: str) -> str:
    """The `_id` of a server's counts for a month (`YYYY-MM`)."""
    return f"{guild_id}|{month}"


class Counts(Repository):
    """Every server's daily counts, a month per document."""

    async def increment(
        self, increments: Mapping[str, Mapping[str, int]], moment: datetime
    ) -> Any:
        """Add `{count_id: {"days.dd.metric": amount}}` in one round trip."""
        if not increments:
            return None

        operations = []
        for document_id, fields in increments.items():
            guild_id, month = document_id.split("|", 1)
            operations.append(
                UpdateOne(
                    {"_id": document_id},
                    {
                        "$inc": dict(fields),
                        "$set": {"updated_at": moment},
                        "$setOnInsert": {"guild_id": guild_id, "month": month},
                    },
                    upsert=True,
                )
            )
        return await self._collection.bulk_write(operations, ordered=False)

    async def find_by_guild(
        self, guild_id: Any, since_month: Optional[str] = None
    ) -> List[Document]:
        """A server's monthly counts, newest month first, from `since_month` on when
        given."""
        query: Dict[str, Any] = {"guild_id": str(guild_id)}
        if since_month:
            query["month"] = {"$gte": since_month}
        return await self._collection.find(query).sort("month", -1).to_list()

    async def find_by_month(self, month: str) -> List[Document]:
        """Every server's counts for one month."""
        return await self._collection.find({"month": month}).to_list()

    async def delete_by_guild(self, guild_id: Any) -> int:
        """Delete every month of a server's counts."""
        result = await self._collection.delete_many({"guild_id": str(guild_id)})
        return result.deleted_count

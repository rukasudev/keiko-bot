"""guild.analytics_archives: the months the monthly archive already posted."""

from typing import Any, Mapping, Optional

from app.data.collection import Document, Repository


class Archives(Repository):
    """The months the monthly archive posted, one document per `YYYY-MM`."""

    async def find_archived_month(self, month: str) -> Optional[Document]:
        """The marker of `month`, when the archive posted it."""
        return await self._collection.find_one({"_id": month})

    async def mark_month_archived(self, month: str, fields: Mapping[str, Any]) -> Any:
        """Record that the archive posted `month`, with what it posted."""
        return await self._collection.update_one(
            {"_id": month}, {"$set": dict(fields)}, upsert=True
        )

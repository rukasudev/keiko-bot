"""guild.logs, the debug log records, and guild.logs_files, how far the logs files
channel is cleared."""

from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List, Mapping, Optional, Tuple

from app.data.collection import Document, Repository


def day_bounds(day: datetime) -> Tuple[datetime, datetime]:
    """The start of `day` in UTC and the start of the next."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


class Logs(Repository):
    """The debug log records, kept 30 days."""

    async def insert_logs(self, documents: List[Dict[str, Any]]) -> Any:
        """Unordered so one rejected document never costs the rest of the batch."""
        if not documents:
            return None
        return await self._collection.insert_many(documents, ordered=False)

    async def iter_logs_for_day(self, day: datetime) -> AsyncGenerator[Document, None]:
        """Stream one day in `ts` order, so the export never holds it all in memory."""
        start, end = day_bounds(day)
        found = self._collection.find({"ts": {"$gte": start, "$lt": end}})
        async for document in found.sort("ts", 1):
            yield document


class LogsFiles(Repository):
    """What Keiko knows of the logs files channel."""

    async def find_retention(self) -> Optional[Document]:
        """How far back the logs files channel is known to hold none of Keiko's files
        past their age."""
        return await self._collection.find_one({"_id": "retention"})

    async def update_retention(self, fields: Mapping[str, Any]) -> Any:
        """Save how far back the channel is cleared."""
        return await self._collection.update_one(
            {"_id": "retention"}, {"$set": dict(fields)}, upsert=True
        )

"""What someone asked for: a command, a button, a setup and its steps, a failure.

Written in the analytics sink's batch and kept 90 days.
"""

from datetime import datetime
from typing import List, Optional, Sequence

from app.data.collection import Document
from app.data.events import BatchedEvents, newest_first, selection


class Interactions(BatchedEvents):
    """events.interactions: what people asked for, kept 90 days."""

    async def find_by_type(
        self,
        types: Sequence[str],
        cog: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[Document]:
        """Every server's interactions of these types, newest first, at most `limit`."""
        query = selection(cog=cog, types=types, since=since)
        found = self._collection.find(query, {"_id": False})
        return await newest_first(found, limit).to_list()

    async def find_by_session(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Document]:
        """One session's interactions, oldest first, at most `limit`."""
        cursor = self._collection.find({"session_id": session_id}, {"_id": False})
        cursor = cursor.sort("ts", 1)
        return await (cursor.limit(limit) if limit else cursor).to_list()

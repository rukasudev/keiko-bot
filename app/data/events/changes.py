"""What someone changed: a feature's config, a server setting, Keiko's presence.

Written at once by `analytics.record` and kept for good.
"""

from typing import Any

from app.data.collection import Document
from app.data.events import EventCollection


class Changes(EventCollection):
    """events.changes: every change, kept for good."""

    async def insert(self, document: Document) -> Any:
        """Write one change."""
        return await self._collection.insert_one(document)

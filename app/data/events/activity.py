"""What a command did on its own: a blocked link, a refused delivery, a greeting.

Written in the analytics sink's batch and kept 90 days.
"""

from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

from app.data.events import BatchedEvents, counted, grouped, selection


class Activity(BatchedEvents):
    """events.activity: what the commands did, kept 90 days."""

    async def count_by_field(
        self,
        guild_id: Any,
        cog: str,
        field: str,
        types: Optional[Sequence[str]] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[Any, int]]:
        """A server's events of one cog counted by the value at `field`, most first."""
        query = selection(guild_id, cog, types, since)
        return await counted(
            await self._collection.aggregate(grouped(query, field, limit))
        )

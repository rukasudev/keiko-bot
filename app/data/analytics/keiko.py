"""analytics.keiko: Keiko's own counters, summed over every server, one per `_id`."""

from datetime import datetime
from typing import Any, List, Mapping, Optional, Sequence

from pymongo import UpdateOne

from app.data.collection import Document, Repository


class Keiko(Repository):
    """Keiko's counters over every server."""

    async def increment(
        self,
        increments: Mapping[str, Mapping[str, int]],
        moment: datetime,
        on_insert: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> Any:
        """Add `{_id: {path: amount}}` in one round trip, `on_insert` naming a new
        counter."""
        if not increments:
            return None

        labels = on_insert or {}
        operations = [
            UpdateOne(
                {"_id": counter_id},
                {
                    "$inc": dict(fields),
                    "$set": {"updated_at": moment},
                    **(
                        {"$setOnInsert": dict(labels[counter_id])}
                        if labels.get(counter_id)
                        else {}
                    ),
                },
                upsert=True,
            )
            for counter_id, fields in increments.items()
        ]
        return await self._collection.bulk_write(operations, ordered=False)

    async def find_counters(self, counter_ids: Sequence[str]) -> List[Document]:
        """The counters named, those that exist."""
        return await self._collection.find(
            {"_id": {"$in": list(counter_ids)}}
        ).to_list()

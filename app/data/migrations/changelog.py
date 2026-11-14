"""migrations.changelog: one entry per migration file applied, written by the runner."""

from typing import Set

from app.data.collection import Repository


class Changelog(Repository):
    """The ledger the boot reads to refuse a database its code's migrations miss."""

    async def applied_ids(self) -> Set[str]:
        """The ids of every migration the ledger records."""
        found = self._collection.find({}, {"_id": 1})
        return {str(entry["_id"]) async for entry in found}

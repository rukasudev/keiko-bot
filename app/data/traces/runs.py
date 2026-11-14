"""traces.runs: the units of work that failed or took too long, one per run."""

from typing import Any, Mapping, Sequence

from app.data.collection import Repository


class Runs(Repository):
    """The runs kept for debugging."""

    async def insert_runs(self, runs: Sequence[Mapping[str, Any]]) -> Any:
        """Keep every run under an id of its own; its trace id is a field."""
        if not runs:
            return None
        return await self._collection.insert_many(
            [dict(run) for run in runs], ordered=False
        )

    async def delete_by_guild(self, guild_id: Any) -> int:
        """Delete every run of a server."""
        result = await self._collection.delete_many({"guild_id": str(guild_id)})
        return result.deleted_count

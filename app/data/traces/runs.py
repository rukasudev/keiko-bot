"""`traces.runs`: the units of work that failed or took too long, one per run."""
from typing import Any, Mapping, Sequence

from app import mongo_client


def insert_runs(runs: Sequence[Mapping[str, Any]]) -> Any:
    """Keep every run under an id of its own; its trace id is a field, not the key."""
    if not runs:
        return None
    return mongo_client.traces.runs.insert_many(
        [dict(run) for run in runs], ordered=False
    )


def delete_by_guild(guild_id: str) -> int:
    """Delete every run of a server."""
    return mongo_client.traces.runs.delete_many({"guild_id": str(guild_id)}).deleted_count

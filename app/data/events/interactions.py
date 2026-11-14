"""What someone asked for: a command, a button, a setup and its steps, a failure.

Written in the analytics sink's batch and kept 90 days.
"""

from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Sequence

from app import mongo_client
from app.data.events import counted, grouped, newest_first, selection


def insert_many(documents: List[Dict[str, Any]]) -> Any:
    """Write a batch. Unordered so one bad document keeps the rest."""
    return mongo_client.events.interactions.insert_many(documents, ordered=False)


def find_by_guild(
    guild_id: Any,
    cog: Optional[str] = None,
    types: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """A server's interactions, newest first, at most `limit`."""
    query = selection(guild_id, cog, types, since)
    return newest_first(
        mongo_client.events.interactions.find(query, {"_id": False}), limit
    )


def find_by_type(
    types: Sequence[str],
    cog: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Every server's interactions of these types, newest first, at most `limit`."""
    query = selection(cog=cog, types=types, since=since)
    return newest_first(
        mongo_client.events.interactions.find(query, {"_id": False}), limit
    )


def find_by_session(session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """One session's interactions, oldest first, at most `limit`."""
    cursor = mongo_client.events.interactions.find(
        {"session_id": session_id}, {"_id": False}
    ).sort("ts", 1)
    return list(cursor.limit(limit) if limit else cursor)


def count_by_type(
    types: Sequence[str],
    guild_id: Any = None,
    cog: Optional[str] = None,
    since: Optional[datetime] = None,
) -> Dict[str, int]:
    """How many interactions of each type, in one server or in all of them, by type."""
    query = selection(guild_id, cog, types, since)
    rows = mongo_client.events.interactions.aggregate(grouped(query, "type"))
    return dict(counted(rows))


def iter_between(start: datetime, end: datetime) -> Iterator[Dict[str, Any]]:
    """Every interaction from `start` up to `end`, oldest first, streamed."""
    yield from mongo_client.events.interactions.find(
        {"ts": {"$gte": start, "$lt": end}}
    ).sort("ts", 1)


def delete_by_guild(guild_id: Any) -> int:
    """Erase a server's interactions and count them."""
    return mongo_client.events.interactions.delete_many(
        {"guild_id": str(guild_id)}
    ).deleted_count

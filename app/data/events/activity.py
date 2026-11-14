"""What a command did on its own: a blocked link, a refused delivery, a greeting.

Written in the analytics sink's batch and kept 90 days.
"""

from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from app import mongo_client
from app.data.events import counted, grouped, newest_first, selection


def insert_many(documents: List[Dict[str, Any]]) -> Any:
    """Write a batch. Unordered so one bad document keeps the rest."""
    return mongo_client.events.activity.insert_many(documents, ordered=False)


def find_by_guild(
    guild_id: Any,
    cog: Optional[str] = None,
    types: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
    actor_id: Any = None,
) -> List[Dict[str, Any]]:
    """A server's activity, or one actor's in it, newest first, at most `limit`."""
    query = selection(guild_id, cog, types, since, actor_id)
    return newest_first(mongo_client.events.activity.find(query, {"_id": False}), limit)


def find_by_type(
    types: Sequence[str], since: Optional[datetime] = None, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Every server's activity of these types, newest first, at most `limit`."""
    query = selection(types=types, since=since)
    return newest_first(mongo_client.events.activity.find(query, {"_id": False}), limit)


def count_by_type(
    types: Sequence[str],
    guild_id: Any = None,
    cog: Optional[str] = None,
    since: Optional[datetime] = None,
) -> Dict[str, int]:
    """How many events of each type, in one server or in all of them, by type."""
    query = selection(guild_id, cog, types, since)
    rows = mongo_client.events.activity.aggregate(grouped(query, "type"))
    return dict(counted(rows))


def count_by_field(
    guild_id: Any,
    cog: str,
    field: str,
    types: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Tuple[Any, int]]:
    """A server's events of one cog counted by the value at `field`, most first."""
    query = selection(guild_id, cog, types, since)
    rows = mongo_client.events.activity.aggregate(grouped(query, field, limit))
    return counted(rows)


def iter_between(start: datetime, end: datetime) -> Iterator[Dict[str, Any]]:
    """Every event from `start` up to `end`, oldest first, streamed rather than held."""
    yield from mongo_client.events.activity.find({"ts": {"$gte": start, "$lt": end}}).sort(
        "ts", 1
    )


def delete_by_guild(guild_id: Any, cog: Optional[str] = None) -> int:
    """Erase a server's activity, or one cog's, and count it."""
    query = selection(guild_id, cog)
    return mongo_client.events.activity.delete_many(query).deleted_count

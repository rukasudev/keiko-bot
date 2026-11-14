"""What someone changed: a feature's config, a server setting, Keiko's presence.

Written at once by `analytics.record` and kept for good.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from app import mongo_client
from app.data.events import counted, grouped, newest_first, selection


def insert(document: Dict[str, Any]) -> Any:
    """Write one change."""
    return mongo_client.events.changes.insert_one(document)


def find_by_guild(
    guild_id: Any,
    cog: Optional[str] = None,
    types: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """A server's changes, newest first, at most `limit`."""
    query = selection(guild_id, cog, types, since)
    return newest_first(mongo_client.events.changes.find(query, {"_id": False}), limit)


def count_by_type(
    guild_id: Any,
    cog: Optional[str] = None,
    types: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
) -> Dict[str, int]:
    """How many changes of each type a server has, by type."""
    query = selection(guild_id, cog, types, since)
    rows = mongo_client.events.changes.aggregate(grouped(query, "type"))
    return dict(counted(rows))


def count_since(types: Sequence[str], since: datetime) -> Dict[str, int]:
    """How many changes of each type every server had since `since`, by type."""
    query = selection(types=types, since=since)
    rows = mongo_client.events.changes.aggregate(grouped(query, "type"))
    return dict(counted(rows))


def delete_by_guild(guild_id: Any, cog: Optional[str] = None) -> int:
    """Erase a server's changes, or one cog's, and count them."""
    query = selection(guild_id, cog)
    return mongo_client.events.changes.delete_many(query).deleted_count

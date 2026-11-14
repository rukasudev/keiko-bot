"""The `events` database: what happened, one envelope in three collections.

`changes` is someone changing something, `activity` a command acting, and
`interactions` someone asking for something. The filters and the grouping the three
share live here; each module sends them to its own collection.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple


def selection(
    guild_id: Any = None,
    cog: Optional[str] = None,
    types: Optional[Sequence[str]] = None,
    since: Optional[datetime] = None,
    actor_id: Any = None,
) -> Dict[str, Any]:
    """The filter of the events that match every value given, in index order."""
    query: Dict[str, Any] = {}
    if guild_id is not None:
        query["guild_id"] = str(guild_id)
    if cog is not None:
        query["cog"] = cog
    if actor_id is not None:
        query["actor.id"] = str(actor_id)
    if types is not None:
        query["type"] = {"$in": list(types)}
    if since is not None:
        query["ts"] = {"$gte": since}
    return query


def newest_first(cursor: Any, limit: Optional[int]) -> List[Dict[str, Any]]:
    """The cursor's events newest first, at most `limit` of them read."""
    cursor = cursor.sort("ts", -1)
    if limit:
        cursor = cursor.limit(limit)
    return list(cursor)


def grouped(query: Dict[str, Any], field: str, limit: Optional[int] = None) -> List[Any]:
    """The pipeline counting the matching events by the value at `field`, most first."""
    pipeline: List[Dict[str, Any]] = [
        {"$match": query},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
    ]
    if limit:
        pipeline.append({"$limit": limit})
    return pipeline


def counted(rows: Any) -> List[Tuple[Any, int]]:
    """The rows of a grouping as `(value, count)` pairs, in their order."""
    return [(row["_id"], row["count"]) for row in rows]

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List

from app import mongo_client


def insert_logs(documents: List[Dict[str, Any]]):
    """Unordered so one rejected document never costs the rest of the batch."""
    if not documents:
        return None
    return mongo_client.guild.logs.insert_many(documents, ordered=False)


def day_bounds(day: datetime):
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def iter_logs_for_day(day: datetime) -> Iterator[Dict[str, Any]]:
    """Stream one day in `ts` order, so the export never holds it all in memory."""
    start, end = day_bounds(day)
    cursor = mongo_client.guild.logs.find({"ts": {"$gte": start, "$lt": end}}).sort("ts", 1)
    for document in cursor:
        yield document

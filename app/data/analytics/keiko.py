"""`analytics.keiko`: Keiko's own counters, summed over every server, one per `_id`."""
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pymongo import UpdateOne

from app import mongo_client


def increment(
    increments: Mapping[str, Mapping[str, int]],
    moment: datetime,
    on_insert: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Any:
    """Add `{_id: {path: amount}}` in one round trip, `on_insert` naming a new counter."""
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
    return mongo_client.analytics.keiko.bulk_write(operations, ordered=False)


def find_counters(counter_ids: Sequence[str]) -> List[Dict[str, Any]]:
    """The counters named, those that exist."""
    return list(mongo_client.analytics.keiko.find({"_id": {"$in": list(counter_ids)}}))

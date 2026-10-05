"""The months of analytics events already posted as files on the logs channel."""
from typing import Any, Dict, Optional

from app import mongo_client


def find_archived_month(month: str) -> Optional[Dict[str, Any]]:
    return mongo_client.guild.analytics_archives.find_one({"_id": month})


def mark_month_archived(month: str, fields: Dict[str, Any]):
    return mongo_client.guild.analytics_archives.update_one(
        {"_id": month}, {"$set": fields}, upsert=True
    )

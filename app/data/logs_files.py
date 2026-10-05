"""How far back the logs files channel is known to hold none of Keiko's files past their age."""
from typing import Any, Dict, Optional

from app import mongo_client


def find_retention() -> Optional[Dict[str, Any]]:
    return mongo_client.guild.logs_files.find_one({"_id": "retention"})


def update_retention(fields: Dict[str, Any]):
    return mongo_client.guild.logs_files.update_one(
        {"_id": "retention"}, {"$set": fields}, upsert=True
    )

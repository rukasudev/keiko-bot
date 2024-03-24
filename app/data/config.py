from typing import Any, Dict

from app import mongo_client
from app.constants import DBConfigs as constants


def find_db_configs() -> Dict[str, Any]:
    return mongo_client.config.data.find_one(
        {constants.KEIKO_STATUS: {"$exists": True}}
    )


def update_db_configs(data: Dict[str, Any]):
    return mongo_client.config.data.update_one({}, {"$set": data})

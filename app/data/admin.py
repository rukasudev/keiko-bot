from typing import Any, Dict

from app import mongo_client
from app.constants import DBConfigs as constants


def find_admin_configs() -> Dict[str, Any]:
    return mongo_client.admin.configs.find_one(
        {constants.ADMIN_GUILD_ID: {"$exists": True}}
    )


def update_admin_configs(data: Dict[str, Any]):
    return mongo_client.admin.configs.update_one({}, {"$set": data})

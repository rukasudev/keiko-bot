from typing import Any, Dict

from app import mongo_client
from app.constants import DBConfigs as constants


def find_db_configs() -> Dict[str, Any]:
    return mongo_client.configs.data.find_one(
        {constants.KEIKO_STATUS: {"$exists": True}}
    )

def find_db_integration_configs(name: str) -> Dict[str, Any]:
    return mongo_client.configs.integrations.find_one(
        {"name": name}
    ).get('configs', {})


def update_db_configs(data: Dict[str, Any]):
    return mongo_client.configs.data.update_one({}, {"$set": data})

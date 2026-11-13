from typing import Any, Dict, List

from app import mongo_client, motor_client
from app.data.util import parse_insert_timestamp


def insert_cog_event(cog_key: str, data: Dict[str, Any]) -> str:
    return mongo_client.events[cog_key].insert_one(data)


def find_cog_events_by_guild_id(guild_id: str, cog_key: str) -> Dict[str, Any]:
    return (
        mongo_client.events[cog_key]
        .find({"guild_id": str(guild_id)}, {"_id": False})
        .sort("datetime", -1)
    )


async def insert_cog_event_async(cog_key: str, data: Dict[str, Any]) -> Any:
    return await motor_client.events[cog_key].insert_one(data)


async def insert_error_by_command_async(cog_key: str, data: Dict[str, Any]) -> Any:
    data = parse_insert_timestamp(data)
    data["command_key"] = cog_key
    return await motor_client.audit.errors.insert_one(data)


async def find_cog_events_by_guild_id_async(guild_id: str, cog_key: str) -> List[Dict[str, Any]]:
    cursor = (
        motor_client.events[cog_key]
        .find({"guild_id": str(guild_id)}, {"_id": False})
        .sort("datetime", -1)
    )
    return await cursor.to_list(length=None)

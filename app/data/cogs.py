from typing import Any, Dict

from app import mongo_client
from app.data.util import parse_insert_timestamp, parse_update_timestamp


def find_cog_by_guild_id(guild_id: str, cog: str) -> Dict[str, Any]:
    return mongo_client.guild[cog].find_one({"guild_id": str(guild_id)})


def insert_cog_by_guild_id(cog: str, data: Dict[str, Any]) -> str:
    data = parse_insert_timestamp(data)
    return mongo_client.guild[cog].insert_one(data)


def insert_cog_event(cog_key: str, data: Dict[str, Any]) -> str:
    return mongo_client.events[cog_key].insert_one(data)


def find_cog_events_by_guild_id(guild_id: str, cog_key: str) -> Dict[str, Any]:
    return (
        mongo_client.events[cog_key]
        .find({"guild_id": str(guild_id)}, {"_id": False})
        .sort("datetime", -1)
    )


def insert_error_by_command(cog_key: str, data: Dict[str, Any]) -> str:
    data = parse_insert_timestamp(data)
    data["command_key"] = cog_key
    return mongo_client.audit.errors.insert_one(data)


def update_cog_by_guild(guild_id: str, cog: str, data: Dict[str, Any]) -> str:
    data = parse_update_timestamp(data)
    return mongo_client.guild[cog].update_one(
        {"guild_id": str(guild_id)}, {"$set": data}
    )


def delete_cog_by_guild_id(guild_id: str, cog: str):
    return mongo_client.guild[cog].delete_one({"guild_id": str(guild_id)})

from typing import Any, Dict

from app import mongo_client
from app.data.util import parse_insert_timestamp, parse_update_timestamp


def find_moderation_by_guild(guild_id: str, data: str) -> Any:
    moderations = mongo_client.guild.moderations.find_one({"guild_id": str(guild_id)})

    return moderations.get(data) if moderations.get(data) else None


def find_moderations_by_guild(guild_id: str) -> dict:
    return mongo_client.guild.moderations.find_one({"guild_id": str(guild_id)})


def insert_moderations_by_guild(data: Dict[str, Any]) -> str:
    data = parse_insert_timestamp(data)

    return mongo_client.guild.moderations.insert_one(data)


def update_moderations_by_guild(guild_id: str, data: str, value: bool):
    new_data = {data: value}
    new_data = parse_update_timestamp(new_data)

    return mongo_client.guild.moderations.update_one(
        {"guild_id": str(guild_id)}, {"$set": new_data}
    )

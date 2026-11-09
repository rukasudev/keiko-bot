from typing import Any, Dict, List, Set

from app import mongo_client
from app.data.util import parse_insert_timestamp, parse_update_timestamp


def find_moderation_by_guild(guild_id: str, data: str) -> Any:
    moderations = mongo_client.guild.moderations.find_one({"guild_id": str(guild_id)})
    if not moderations:
        return None
    return moderations.get(data) or None


def find_moderations_by_guild(guild_id: str) -> dict:
    return mongo_client.guild.moderations.find_one({"guild_id": str(guild_id)})


def insert_moderations_by_guild(data: Dict[str, Any]) -> str:
    data = parse_insert_timestamp(data)

    return mongo_client.guild.moderations.insert_one(data)


def count_moderations_by_owner(owner_id: str) -> int:
    return mongo_client.guild.moderations.count_documents({"owner_id": str(owner_id)})


def find_moderations_by_owner(owner_id: str) -> list:
    return list(mongo_client.guild.moderations.find({"owner_id": str(owner_id)}))


def update_moderations_by_guild(guild_id: str, data: str, value: bool):
    new_data = {data: value}
    new_data = parse_update_timestamp(new_data)

    return mongo_client.guild.moderations.update_one(
        {"guild_id": str(guild_id)}, {"$set": new_data}
    )


def upsert_moderations_by_guild(
    guild_id: str, data: str, value: Any, defaults: Dict[str, Any]
) -> Any:
    update = parse_update_timestamp({data: value})
    on_insert = {
        key: default for key, default in defaults.items() if key not in (data, "guild_id")
    }
    on_insert["created_at"] = update["updated_at"]
    return mongo_client.guild.moderations.update_one(
        {"guild_id": str(guild_id)},
        {"$set": update, "$setOnInsert": on_insert},
        upsert=True,
    )


def set_server_setting(guild_id: str, key: str, value: Any) -> Any:
    update = parse_update_timestamp({f"settings.{key}": value})
    return mongo_client.guild.moderations.update_one(
        {"guild_id": str(guild_id)}, {"$set": update}
    )


def insert_server_settings(guild_id: str, settings: Dict[str, Any]) -> Any:
    update = parse_update_timestamp({"settings": settings})
    return mongo_client.guild.moderations.update_one(
        {"guild_id": str(guild_id), "settings": {"$exists": False}}, {"$set": update}
    )


def find_guild_ids_with_settings(guild_ids: List[str]) -> Set[str]:
    documents = mongo_client.guild.moderations.find(
        {"guild_id": {"$in": [str(guild_id) for guild_id in guild_ids]},
         "settings": {"$exists": True}},
        {"guild_id": True, "_id": False},
    )
    return {str(document["guild_id"]) for document in documents}

from app import mongo_client
from typing import Any


def find_cog_by_guild_id(guild_id: str, cog: str) -> dict[str, Any]:
    return mongo_client.guild[cog].find_one({"guild_id": guild_id})


def upsert_cog_by_guild_id(guild_id: str, cog: str, data: dict[str, Any]) -> str:
    return mongo_client.guild[cog].update_one(
        {"guild_id": str(guild_id)}, {"$set": data}, upsert=True
    )


def delete_cog_by_guild_id(guild_id: str, cog: str):
    return mongo_client.guild[cog].delete_one({"guild_id": str(guild_id)})

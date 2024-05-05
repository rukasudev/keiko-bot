from typing import Any, Dict

from app.data import cogs as cogs_data
from app.services.cache import remove_cog_cache_by_guild


def insert_cog_by_guild(guild_id: str, cog: str, data: Dict[str, Any]):
    if not data.get("guild_id"):
        data["guild_id"] = str(guild_id)

    return cogs_data.insert_cog_by_guild_id(cog, data)


def insert_cog_event(
    guild_id: str,
    cog_key: str,
    event: str,
    date: str,
    user_id: str,
):
    data = {
        "guild_id": guild_id,
        "cog_key": cog_key,
        "user_id": user_id,
        "datetime": date,
        "event": event,
    }

    return cogs_data.insert_cog_event(cog_key, data)


def find_cog_events_by_guild(guild_id: str, cog_key: str):
    return cogs_data.find_cog_events_by_guild_id(guild_id, cog_key)


def update_cog_by_guild(guild_id: str, cog_key: str, data: Dict[str, Any]):
    if not data.get("guild_id"):
        data["guild_id"] = str(guild_id)

    remove_cog_cache_by_guild(guild_id, cog_key)

    return cogs_data.update_cog_by_guild(guild_id, cog_key, data)


def delete_cog_by_guild(guild_id: str, cog_key: str):
    if guild_id == "":
        return

    remove_cog_cache_by_guild(guild_id, cog_key)

    return cogs_data.delete_cog_by_guild_id(guild_id, cog_key)

from typing import Any, Dict

from bson import json_util

from app import redis_client
from app.data import cogs as cogs_data


def clear_cache_commands_by_guild(guild_id: str, command_key: str) -> int:
    for key in redis_client.scan_iter(f"{guild_id}@{command_key}:*"):
        redis_client.delete(key)
    return


def set_data_in_redis(key: str, data: Dict[str, Any]):
    redis_client.set(key, json_util.dumps(data))

def set_data_in_redis_with_expiration(key: str, data: Dict[str, Any], expiration: int):
    redis_client.setex(key, expiration, json_util.dumps(data))

def get_data_from_redis(key: str) -> Dict[str, Any]:
    data = redis_client.get(key)
    if data:
        return json_util.loads(data)
    return {}

def increment_redis_key(key: str, increment_by=1):
    return redis_client.incrby(key, increment_by)


def get_cog_data_or_populate(guild_id: str, key: str, manager: bool=False) -> Dict[str, Any]:
    redis_key = f"guild:{guild_id}:cog.{key}"
    data = redis_client.get(redis_key)
    if data:
        data = json_util.loads(data)
        return data if data.get("enabled") or manager else {}

    data = cogs_data.find_cog_by_guild_id(str(guild_id), key)
    if data:
        month_in_seconds = 60 * 60 * 24 * 30
        set_data_in_redis_with_expiration(redis_key, data, month_in_seconds)
        return data if data.get("enabled") or manager else {}

    return data


def remove_cog_cache_by_guild(guild_id: str, key: str):
    redis_key = f"guild:{guild_id}:cog.{key}"
    redis_client.delete(redis_key)


def remove_all_cache_by_guild(guild_id: str):
    keys_to_delete = redis_client.keys(f"guild:{guild_id}:*")
    if keys_to_delete:
        redis_client.delete(*keys_to_delete)

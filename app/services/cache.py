from app import redis_client


def clear_cache_commands_by_guild(guild_id: str, command_key: str) -> int:
    for key in redis_client.scan_iter(f"{guild_id}@{command_key}:*"):
        redis_client.delete(key)
    return

from datetime import datetime
from typing import Any, Dict, Optional

from app.constants import Commands as commands_constants
from app.constants import GuildConstants as guild_constants
from app.data import cogs as cogs_data
from app.data import moderations as moderations_data
from app.services.cache import remove_all_cache_by_guild, remove_cog_cache_by_guild
from app.services.cogs import insert_cog_event, is_feature_on


def update_moderations_by_guild(guild_id: str, key: str, value: Any):
    if not guild_id:
        return None

    return moderations_data.upsert_moderations_by_guild(
        guild_id, key, value, parse_default_moderations(guild_id)
    )


def set_feature_enabled(guild_id: str, key: str, enabled: bool) -> None:
    """Record whether a feature is on: its document, its cache, then its moderations flag."""
    cogs_data.update_cog_by_guild(guild_id, key, {commands_constants.ENABLED_KEY: enabled})
    remove_cog_cache_by_guild(guild_id, key)
    update_moderations_by_guild(guild_id, key, enabled)


def leave_guild(guild_id: str, bot_user_id: str) -> Optional[Dict[str, Any]]:
    """Everything a guild Keiko left needs, in one call a worker thread can run."""
    moderations = pause_all_moderations_by_guild(guild_id, bot_user_id)
    remove_all_cache_by_guild(guild_id)
    return moderations


def pause_all_moderations_by_guild(guild_id: str, bot_user_id: str):
    """Pause every feature that is on in a guild Keiko left, and mark Keiko offline there."""
    moderations = moderations_data.find_moderations_by_guild(guild_id)
    paused = []

    for spec in commands_constants.SETUP_FEATURES:
        key = spec["command_key"]
        document = cogs_data.find_cog_by_guild_id(guild_id, key)
        if not is_feature_on(str(guild_id), key, document):
            continue

        set_feature_enabled(guild_id, key, False)
        paused.append(key)
        insert_cog_event(
            str(guild_id),
            key,
            commands_constants.PAUSED_KEY,
            date=datetime.fromisoformat(datetime.now().isoformat()),
            user_id=bot_user_id,
        )

    for key, value in (moderations or {}).items():
        if value is True and key not in (*paused, guild_constants.IS_BOT_ONLINE):
            moderations_data.update_moderations_by_guild(guild_id, key, False)

    moderations_data.update_moderations_by_guild(
        guild_id, guild_constants.IS_BOT_ONLINE, False
    )

    return moderations


def insert_moderations_by_guild(guild_id: str, data: Dict[str, Any] = None, owner_id: str = None) -> str:
    default_data = parse_default_moderations(guild_id, owner_id=owner_id)

    return moderations_data.insert_moderations_by_guild(data or default_data)


def parse_default_moderations(guild_id: str, owner_id: str = None) -> Dict[str, Any]:
    data = dict(guild_constants.COGS_MODERATIONS_COMMANDS_DEFAULT)
    data["guild_id"] = str(guild_id)
    if owner_id:
        data["owner_id"] = str(owner_id)
    return data


def insert_error_by_command(cog_key: str, error_message: str):
    if not isinstance(error_message, str):
        return None

    data = {"error_message": error_message}
    return cogs_data.insert_error_by_command(cog_key, data)

from datetime import datetime
from typing import Any, Dict

from app.constants import Commands as commands_constants
from app.constants import GuildConstants as guild_constants
from app.data import cogs as cogs_data
from app.data import moderations as moderations_data
from app.services.cogs import insert_cog_event, update_cog_by_guild


def update_moderations_by_guild(guild_id: str, key: str, value: str):
    if not guild_id:
        return None

    moderations = moderations_data.find_moderations_by_guild(guild_id)
    if not moderations:
        data = parse_default_moderations(guild_id)
        data[key] = value

        return moderations_data.insert_moderations_by_guild(data)

    return moderations_data.update_moderations_by_guild(
        guild_id=guild_id, data=key, value=value
    )


def pause_all_moderations_by_guild(guild_id: str, bot_user_id: str):
    moderations = moderations_data.find_moderations_by_guild(guild_id)
    if not moderations:
        return None

    for key, value in moderations.items():

        if not isinstance(value, bool) or not value:
            continue

        update_moderations_by_guild(guild_id, key, False)
        update_cog_by_guild(
            guild_id=guild_id, cog_key=key, data={commands_constants.ENABLED_KEY: False}
        )

        if key == guild_constants.IS_BOT_ONLINE:
            continue

        insert_cog_event(
            str(guild_id),
            key,
            commands_constants.PAUSED_KEY,
            date=datetime.fromisoformat(datetime.now().isoformat()),
            user_id=bot_user_id,
        )

    return moderations


def pause_moderations_by_guild(guild_id: str, key: str):
    update_moderations_by_guild(guild_id, key, False)
    return update_cog_by_guild(
        guild_id=guild_id, cog_key=key, data={commands_constants.ENABLED_KEY: False}
    )


def unpause_moderations_by_guild(guild_id: str, key: str):
    update_moderations_by_guild(guild_id, key, True)
    return update_cog_by_guild(
        guild_id=guild_id, cog_key=key, data={commands_constants.ENABLED_KEY: True}
    )


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

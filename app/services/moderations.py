import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import available_timezones

import app as app_module
from app.constants import Commands as commands_constants
from app.constants import DBConfigs
from app.constants import GuildConstants as guild_constants
from app.constants import supported_locales
from app.data import cogs as cogs_data
from app.data import moderations as moderations_data
from app.exceptions import InvalidServerSetting, ServerSettingsMissing
from app.services.cache import (
    forget_remembered,
    get_cog_data_or_populate,
    remembered,
    remove_all_cache_by_guild,
    remove_cog_cache_by_guild,
)
from app.services.cogs import insert_cog_event, is_feature_on
from app.services.utils import parse_locale, speak


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


def join_guild(
    guild_id: int, owner_id: str, settings: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], int]:
    """Mark Keiko online in a server it joined, with `settings` where it has none saved:
    the server's earlier record, if any, and its owner's server count."""
    exist = moderations_data.find_moderations_by_guild(guild_id)

    if not exist:
        insert_moderations_by_guild(guild_id, owner_id=owner_id)
    else:
        update_moderations_by_guild(guild_id, guild_constants.IS_BOT_ONLINE, True)
        update_moderations_by_guild(guild_id, "owner_id", owner_id)

    moderations_data.insert_server_settings(guild_id, settings)
    remove_cog_cache_by_guild(str(guild_id), commands_constants.MODERATIONS_KEY)
    return exist, moderations_data.count_moderations_by_owner(owner_id)


def guilds_without_settings(guild_ids: Sequence[str]) -> List[str]:
    """The servers among `guild_ids` with no saved settings, found with one query."""
    recorded = moderations_data.find_guild_ids_with_settings(list(guild_ids))
    return [guild_id for guild_id in guild_ids if str(guild_id) not in recorded]


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


def default_prefix() -> str:
    """The prefix of the configs document: Keiko's in a DM and a new server's first one."""
    return str(app_module.bot.config.PREFIX)


def guess_server_settings(guild: Any) -> Dict[str, Any]:
    """The settings Keiko guesses for a server it records for the first time."""
    locale = str(guild.preferred_locale)
    timezones = commands_constants.SERVER_GUESSED_TIMEZONES
    return {
        commands_constants.SERVER_LANGUAGE_KEY: parse_locale(locale),
        commands_constants.SERVER_TIMEZONE_KEY: timezones.get(
            locale, commands_constants.SERVER_GUESSED_TIMEZONE
        ),
        commands_constants.SERVER_PREFIX_KEY: default_prefix(),
    }


def server_settings(guild_id: str) -> Dict[str, Any]:
    """The three saved settings of a server, through the cache; ServerSettingsMissing when
    it has none or lacks one."""
    moderations = get_cog_data_or_populate(
        str(guild_id), commands_constants.MODERATIONS_KEY, manager=True
    )
    settings = (moderations or {}).get(commands_constants.SERVER_SETTINGS_KEY)
    if not isinstance(settings, dict) or any(
        key not in settings for key in commands_constants.SERVER_SETTING_KEYS
    ):
        raise ServerSettingsMissing(guild_id)
    return settings


def server_setting(guild_id: str, key: str) -> str:
    """One saved setting of a server, through the cache; ServerSettingsMissing when it has none."""
    return str(server_settings(guild_id)[key])


async def server_language(guild_id: str) -> str:
    """The language Keiko speaks to a whole server."""
    return await _server_setting(guild_id, commands_constants.SERVER_LANGUAGE_KEY)


async def interaction_language(interaction: Any) -> str:
    """The language Keiko answers an interaction in, from then on its locale too: the
    server's saved one inside a server, the person's own in a DM or a server not
    recorded yet."""
    language = parse_locale(interaction.locale)
    if interaction.guild_id is not None:
        try:
            language = await server_language(str(interaction.guild_id))
        except ServerSettingsMissing:
            pass
    return speak(interaction, language)


async def server_timezone(guild_id: str) -> str:
    """The IANA time zone of a server."""
    return await _server_setting(guild_id, commands_constants.SERVER_TIMEZONE_KEY)


async def server_prefix(guild_id: str) -> str:
    """The chat prefix of a server, kept in this process between saves."""
    def read() -> str:
        return server_setting(guild_id, commands_constants.SERVER_PREFIX_KEY)

    return await remembered(
        _prefix_memo(guild_id), read, DBConfigs.COG_CACHE_TTL_SECONDS
    )


def save_server_setting(guild_id: str, key: str, value: Any) -> None:
    """Save one setting of a server: InvalidServerSetting when its key cannot hold `value`,
    ServerSettingsMissing when the server has no settings to change."""
    if not _valid_setting(key, value):
        raise InvalidServerSetting(key)
    server_settings(guild_id)
    moderations_data.set_server_setting(guild_id, key, value)
    remove_cog_cache_by_guild(str(guild_id), commands_constants.MODERATIONS_KEY)
    forget_remembered(_prefix_memo(guild_id))


async def _server_setting(guild_id: str, key: str) -> str:
    return await asyncio.to_thread(server_setting, guild_id, key)


def _prefix_memo(guild_id: Any) -> str:
    return f"guild:{guild_id}:settings.prefix"


def _valid_setting(key: str, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if key == commands_constants.SERVER_LANGUAGE_KEY:
        return value in [locale.lower() for locale in supported_locales]
    if key == commands_constants.SERVER_TIMEZONE_KEY:
        return value in available_timezones()
    if key == commands_constants.SERVER_PREFIX_KEY:
        return value in commands_constants.SERVER_PREFIXES
    return False

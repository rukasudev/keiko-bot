from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import available_timezones

import app as app_module
from app.constants import Commands as commands_constants
from app.constants import DBConfigs
from app.constants import supported_locales
from app.data import data
from app.exceptions import InvalidServerSetting, ServerSettingsMissing
from app.core.cache import (
    forget_remembered,
    remembered,
    remove_all_cache_by_guild,
    remove_cog_cache_by_guild,
)
from app.core import analytics
from app.core.cogs import is_feature_on
from app.core.utils import parse_locale, speak
from app.core.threads import to_thread


async def set_feature_enabled(
    guild_id: str, key: str, enabled: bool, updated_by: Optional[str] = None
) -> None:
    """Record whether a feature is on, and who said so when a person did: its
    document, then its cache."""
    await data().guild_cogs.update_cog_by_guild(
        guild_id, key, {commands_constants.ENABLED_KEY: enabled}, updated_by
    )
    await to_thread(remove_cog_cache_by_guild, guild_id, key)


async def join_guild(
    guild_id: int, owner_id: str, settings: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], int]:
    """Record Keiko in a server it joined, with `settings` where it has none saved:
    the server's earlier record, if any, and its owner's server count."""
    exist = await data().guild_settings.find_settings(guild_id)

    await data().guild_settings.record_join(guild_id, owner_id, settings)
    forget_remembered(_settings_memo(guild_id))
    return exist, await data().guild_settings.count_servers_by_owner(owner_id)


async def guilds_without_settings(guild_ids: Sequence[str]) -> List[str]:
    """The servers among `guild_ids` with no saved settings, found with one query."""
    recorded = await data().guild_settings.find_guild_ids_with_settings(list(guild_ids))
    return [guild_id for guild_id in guild_ids if str(guild_id) not in recorded]


async def leave_guild(guild_id: str, bot_user_id: str) -> List[str]:
    """Everything a guild Keiko left needs: the features it paused."""
    paused = await pause_features(guild_id, bot_user_id)
    await data().guild_settings.record_leave(guild_id)
    await to_thread(remove_all_cache_by_guild, guild_id)
    forget_remembered(_settings_memo(guild_id))
    return paused


async def pause_features(guild_id: str, bot_user_id: str) -> List[str]:
    """Pause every feature that is on in a guild Keiko left."""
    paused = []

    documents = await data().guild_cogs.find_cogs_by_guild(guild_id)

    for spec in commands_constants.SETUP_FEATURES:
        key = spec["command_key"]
        if not is_feature_on(documents.get(key)):
            continue

        await set_feature_enabled(guild_id, key, False)
        paused.append(key)
        analytics.record(
            "feature.paused",
            guild_id=guild_id,
            cog=key,
            actor=analytics.actor("bot", bot_user_id),
            props={"source": "internal"},
        )

    return paused


def default_prefix() -> str:
    """Keiko's prefix from keiko.settings: the one in a DM and a new server's first one."""
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


async def server_settings(guild_id: str) -> Dict[str, Any]:
    """The three saved settings of a server, kept in this process between saves;
    ServerSettingsMissing when it has none or lacks one."""
    options = await remembered(
        _settings_memo(guild_id),
        lambda: _saved_options(guild_id),
        DBConfigs.COG_CACHE_TTL_SECONDS,
    )
    return _complete(guild_id, options)


async def server_setting(guild_id: str, key: str) -> str:
    """One saved setting of a server, through the cache; ServerSettingsMissing when it has none."""
    return str((await server_settings(guild_id))[key])


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
    """The chat prefix of a server."""
    return await _server_setting(guild_id, commands_constants.SERVER_PREFIX_KEY)


async def save_server_setting(guild_id: str, key: str, value: Any) -> None:
    """Save one setting of a server: InvalidServerSetting when its key cannot hold `value`,
    ServerSettingsMissing when the server has no settings to change."""
    if not _valid_setting(key, value):
        raise InvalidServerSetting(key)
    await server_settings(guild_id)
    await data().guild_settings.set_option(guild_id, key, value)
    forget_remembered(_settings_memo(guild_id))


async def _server_setting(guild_id: str, key: str) -> str:
    return str((await server_settings(guild_id))[key])


def _settings_memo(guild_id: Any) -> str:
    return DBConfigs.SETTINGS_MEMO_KEY.format(guild_id=guild_id)


async def _saved_options(guild_id: str) -> Optional[Dict[str, Any]]:
    document = await data().guild_settings.find_settings(guild_id)
    if document is None:
        return None
    keys = commands_constants.SERVER_SETTING_KEYS
    return {key: document[key] for key in keys if key in document}


def _complete(guild_id: str, options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(options, dict) or any(
        key not in options for key in commands_constants.SERVER_SETTING_KEYS
    ):
        raise ServerSettingsMissing(guild_id)
    return dict(options)


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

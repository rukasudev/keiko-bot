from importlib import import_module
from typing import Any, Callable, Dict, Final, List, Optional

from app.constants import Commands as constants
from app.data import cogs as cogs_data
from app.services import analytics
from app.services.cache import remove_cog_cache_by_guild

LIFECYCLE_ANALYTICS_EVENTS = {
    constants.ENABLED_KEY: "feature.enabled",
    constants.EDITED_KEY: "config.changed",
    constants.PAUSED_KEY: "feature.paused",
    constants.UNPAUSED_KEY: "feature.unpaused",
    constants.DISABLED_KEY: "feature.disabled",
    constants.ADDED_KEY: "feature.item_added",
    constants.REMOVED_KEY: "feature.item_removed",
}

# A feature whose storage shape is not the shape its form names. A YAML-driven
# cog stores exactly the keys its form declares, so its document *is* that shape
# and the default read below is enough. Birthdays are the exception the reports
# kept tripping on: the channel is `channel_id`, three settings are folded into
# a nested `default_message`, and the birthdays themselves live in the
# `reminders` database. A reader walking the raw document finds none of those
# keys and concludes, wrongly, that nobody uses the settings.
#
# The translation already exists — it is what the manager renders — so this
# points at it instead of describing the storage a second time. Providers are
# named as `module:function` and imported on use, so a report can depend on a
# feature service without the service importing the report back.
CONFIG_STATE_PROVIDERS: Final[Dict[str, str]] = {
    constants.REMINDERS_BIRTHDAY_KEY: (
        "app.services.reminders_birthdays:birthday_config_states"
    ),
}


def feature_config_states(feature: str) -> List[Dict[str, Any]]:
    """Every guild's saved configuration, keyed as the feature's form names it."""
    provider = CONFIG_STATE_PROVIDERS.get(feature)
    if not provider:
        return cogs_data.find_all_cogs(feature)
    return resolve_config_state_provider(provider)()


def resolve_config_state_provider(path: str) -> Callable[[], List[Dict[str, Any]]]:
    module_name, function_name = path.split(":")
    return getattr(import_module(module_name), function_name)


def insert_cog_by_guild(guild_id: str, cog: str, data: Dict[str, Any]):
    if not data.get("guild_id"):
        data["guild_id"] = str(guild_id)

    remove_cog_cache_by_guild(guild_id, cog)

    return cogs_data.insert_cog_by_guild_id(cog, data)


def insert_cog_event(
    guild_id: str,
    cog_key: str,
    event: str,
    date: str,
    user_id: str,
    source: Optional[str] = None,
    session_id: Optional[str] = None,
    **props: Any,
):
    """Writes the permanent audit record and emits its analytics counterpart.

    Every state change already passes through here, so the product view comes
    for free — no handler gains an analytics call of its own.
    """
    data = {
        "guild_id": guild_id,
        "cog_key": cog_key,
        "user_id": user_id,
        "datetime": date,
        "event": event,
    }

    analytics_event = LIFECYCLE_ANALYTICS_EVENTS.get(event)
    if analytics_event:
        analytics.emit(
            analytics_event,
            guild_id=guild_id,
            user_id=user_id,
            feature=cog_key,
            source=source or "manager",
            session_id=session_id,
            **props,
        )

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

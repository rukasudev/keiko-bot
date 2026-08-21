from typing import Any, Dict, Optional

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

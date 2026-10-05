from typing import Any, Callable, Dict, List, Mapping, Optional

from app.constants import Commands as constants
from app.data import cogs as cogs_data
from app.data import moderations as moderations_data
from app.services import analytics

LIFECYCLE_ANALYTICS_EVENTS = {
    constants.ENABLED_KEY: "feature.enabled",
    constants.EDITED_KEY: "config.changed",
    constants.PAUSED_KEY: "feature.paused",
    constants.UNPAUSED_KEY: "feature.unpaused",
    constants.DISABLED_KEY: "feature.disabled",
    constants.ADDED_KEY: "feature.item_added",
    constants.REMOVED_KEY: "feature.item_removed",
}

def feature_config_states(feature: str) -> List[Dict[str, Any]]:
    """Every guild's saved configuration, keyed as the feature's form names it.

    A feature whose storage shape differs from its form defines `config_states`
    in its service; everything else is a plain read of the collection.
    """
    provider = config_states_provider(feature)
    return provider() if provider else cogs_data.find_all_cogs(feature)


def config_states_provider(feature: str) -> Optional[Callable[[], List[Dict[str, Any]]]]:
    if feature != constants.REMINDERS_BIRTHDAY_KEY:
        return None
    from app.services.reminders_birthdays import config_states

    return config_states


def is_feature_on(guild_id: str, key: str, document: Optional[Mapping[str, Any]]) -> bool:
    """Whether a feature is on: its saved document says so, or for a document saved
    before `enabled` existed, its old moderations flag."""
    if not document:
        return False
    if constants.ENABLED_KEY in document:
        return bool(document[constants.ENABLED_KEY])
    return bool(moderations_data.find_moderation_by_guild(guild_id, key))


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

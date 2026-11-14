from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from app.constants import Commands as constants
from app.data import data

LIFECYCLE_ANALYTICS_EVENTS = {
    constants.ENABLED_KEY: "feature.enabled",
    constants.EDITED_KEY: "config.changed",
    constants.PAUSED_KEY: "feature.paused",
    constants.UNPAUSED_KEY: "feature.unpaused",
    constants.DISABLED_KEY: "feature.disabled",
    constants.ADDED_KEY: "feature.item_added",
    constants.REMOVED_KEY: "feature.item_removed",
}

async def feature_config_states(feature: str) -> List[Dict[str, Any]]:
    """Every guild's saved configuration, keyed as the feature's form names it.

    A feature whose storage shape differs from its form defines `config_states`
    in its service; everything else is a plain read of the collection.
    """
    provider = config_states_provider(feature)
    if provider:
        return await provider()
    return await data().guild_cogs.find_all_cogs(feature)


def config_states_provider(
    feature: str,
) -> Optional[Callable[[], Awaitable[List[Dict[str, Any]]]]]:
    if feature != constants.REMINDERS_BIRTHDAY_KEY:
        return None
    from app.services.reminders_birthdays import config_states

    return config_states


def is_feature_on(document: Optional[Mapping[str, Any]]) -> bool:
    """Whether a feature is on: its saved document says so."""
    return bool(document and document.get(constants.ENABLED_KEY))


def feature_spec(command_key: str) -> Dict[str, Any]:
    """A feature as `Commands.SETUP_FEATURES` lists it, or nothing for an unknown key."""
    return next(
        (spec for spec in constants.SETUP_FEATURES if spec["command_key"] == command_key),
        {},
    )


async def features_on(guild_id: str) -> List[str]:
    """The setup features that are on in a server, by `is_feature_on`."""
    documents = await data().guild_cogs.find_cogs_by_guild(guild_id)
    return [
        spec["command_key"]
        for spec in constants.SETUP_FEATURES
        if is_feature_on(documents.get(spec["command_key"]))
    ]


async def find_changes(guild_id: str, cog: Optional[str] = None) -> List[Dict[str, Any]]:
    """A server's lifecycle changes, or one cog's, newest first, as a history lists them."""
    return await data().events_changes.find_by_guild(
        guild_id,
        cog=cog,
        types=list(LIFECYCLE_ANALYTICS_EVENTS.values()),
        limit=constants.EVENTS_HISTORY_READ_LIMIT,
    )


def lifecycle_key(event_type: str) -> Optional[str]:
    """The lifecycle key of a change's type, the one its history line is named by."""
    return next(
        (key for key, mapped in LIFECYCLE_ANALYTICS_EVENTS.items() if mapped == event_type),
        None,
    )

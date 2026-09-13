"""One module per feature: how its answers persist and what else happens on commit."""

from __future__ import annotations

from app.constants import Commands
from app.forms.features.protocol import FeatureModule


def feature_for(key: str) -> FeatureModule:
    """The feature module behind the form `key`."""
    from app.forms.features import (
        block_links,
        default_roles,
        notifications_twitch,
        notifications_youtube,
        reminders_birthday,
        stream_elements,
        welcome_messages,
    )

    modules: dict[str, FeatureModule] = {
        Commands.BLOCK_LINKS_KEY: block_links.FEATURE,
        Commands.DEFAULT_ROLES_KEY: default_roles.FEATURE,
        Commands.NOTIFICATIONS_TWITCH_KEY: notifications_twitch.FEATURE,
        Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: notifications_youtube.FEATURE,
        Commands.REMINDERS_BIRTHDAY_KEY: reminders_birthday.FEATURE,
        Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY: stream_elements.FEATURE,
        Commands.WELCOME_MESSAGES_KEY: welcome_messages.FEATURE,
    }
    return modules[key]


def feature_keys() -> tuple[str, ...]:
    """Every form key with a feature module."""
    return (
        Commands.BLOCK_LINKS_KEY,
        Commands.DEFAULT_ROLES_KEY,
        Commands.NOTIFICATIONS_TWITCH_KEY,
        Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        Commands.REMINDERS_BIRTHDAY_KEY,
        Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
        Commands.WELCOME_MESSAGES_KEY,
    )

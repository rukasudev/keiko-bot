"""One module per feature: how its answers persist and what else happens on commit."""

from __future__ import annotations

import asyncio
from typing import Any

from app.constants import Commands
from app.settings.features.feature import FeatureModule, OpenContext
from app.settings.form.responses.summary import ResponseView


def feature_for(key: str) -> FeatureModule:
    """The feature module behind `key`; a compiled form without one is generic."""
    from app.settings.features import (
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
    if key in modules:
        return modules[key]
    from app.settings.features.feature import GenericCogFeature
    from app.settings.form.form_yaml import registry

    registry.get(key)
    return GenericCogFeature(key)


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


async def saved_settings(
    key: str, guild_id: str, user_id: str, locale: str, guild: Any = None
) -> tuple[ResponseView, ...]:
    """What a guild has saved for `key`, as the lines a screen would list it.

    The read a screen outside the forms needs: no session is opened, and the
    previews a feature would draw for one are never started.
    """
    from app.settings.form.form_yaml import registry
    from app.settings.form.responses.summary import responses

    feature = feature_for(key)
    opened = await feature.open(OpenContext(guild_id, user_id, locale, guild, None))
    if asyncio.iscoroutine(opened.pending_previews):
        opened.pending_previews.close()

    answers = feature.from_document(opened.document or {})
    return responses(registry.get(key).steps, answers, locale)

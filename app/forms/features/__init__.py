"""How a feature's answers persist: the contract, the generic feature and the lookup."""

from __future__ import annotations

from typing import Any

from app.core.commands import command_for, setup_features
from app.forms.features.feature import FeatureModule, GenericCogFeature, OpenContext
from app.forms.form.form_yaml import registry
from app.forms.form.responses.summary import ResponseView


def feature_for(key: str) -> FeatureModule:
    """The feature module `key` declares, else the generic one of its form."""
    command = command_for(key)
    if command.feature is not None:
        return command.feature
    try:
        registry.get(key)
    except FileNotFoundError:
        raise KeyError(f"{key!r} declares neither a feature nor a form") from None
    return GenericCogFeature(key)


def feature_keys() -> tuple[str, ...]:
    """Every feature Keiko offers, in the order /setup lists them."""
    return tuple(command.key for command in setup_features())


async def saved_settings(
    key: str, guild_id: str, user_id: str, locale: str, guild: Any = None
) -> tuple[ResponseView, ...]:
    """What a guild has saved for `key`, as the lines a screen would list it."""
    context = OpenContext(guild_id, user_id, locale, guild, None)
    return await feature_for(key).saved_views(context)

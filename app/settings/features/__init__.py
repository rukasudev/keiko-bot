"""One module per feature: how its answers persist and what else happens on commit."""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any

from app.constants import Feature
from app.settings.features.feature import FeatureModule, OpenContext
from app.settings.form.responses.summary import ResponseView


def feature_for(key: str) -> FeatureModule:
    """The feature module `Feature` names for `key`, or the generic one it asks for."""
    spec = next(
        (feature.value for feature in Feature if feature.value.key == key), None
    )
    if spec is None:
        raise KeyError(f"{key!r} is not a feature `Feature` declares")

    if spec.module is None:
        from app.settings.features.feature import GenericCogFeature
        from app.settings.form.form_yaml import registry

        registry.get(key)
        return GenericCogFeature(key)

    module: FeatureModule = import_module(f"{__name__}.{spec.module}").FEATURE
    return module


def feature_keys() -> tuple[str, ...]:
    """Every feature Keiko offers, as `Feature` declares them."""
    return tuple(feature.value.key for feature in Feature)


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

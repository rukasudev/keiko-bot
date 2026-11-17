"""Localized copy from the language files, with the same fallback the bot uses."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, cast

from i18n import t, translations

from app.constants import supported_locales
from app.core.facts import Facts, filled
from app.core.i18n import resolved_texts


def normalize_locale(locale: Any) -> str:
    """Map any Discord locale to one of the two the copy exists in."""
    value = str(locale.value if isinstance(locale, Enum) else locale).lower()
    supported = [entry.lower() for entry in supported_locales]
    if value in supported:
        return value
    prefix = value.split("-")[0]
    for entry in supported:
        if entry.startswith(prefix):
            return entry
    return "en-us"


def error_message(block: str, args: Mapping[str, str], locale: str) -> str:
    """The message of the error block `block`, or of `errors.<block>`, its `$name`s
    filled from `args`."""
    named = block if "." in block else f"errors.{block}"
    return filled(text(f"{named}.message", locale), args)


def block_texts(key: str, facts: Facts, locale: str) -> Mapping[str, str]:
    """The texts of the copy block under `key` in `locale`, with the overrides `facts`
    choose and its `$name`s filled."""
    return resolved_texts(key, facts, normalize_locale(locale))


def optional_text(key: str, locale: str) -> str:
    """The copy under `key` for `locale`, or nothing when no file writes the key."""
    return text(key, locale) if translations.has(key, "en-us") else ""


def text(key: str, locale: str) -> str:
    """The copy under `key` for `locale`, English when the key is missing there."""
    try:
        return cast(
            str, t(key, locale=normalize_locale(locale), default=t(key, locale="en-us"))
        )
    except Exception:
        return cast(str, t(key, locale="en-us"))

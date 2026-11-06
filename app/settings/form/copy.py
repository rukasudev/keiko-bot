"""Localized copy from the language files, with the same fallback the bot uses."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, cast

from i18n import t

from app.constants import supported_locales


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


def error_message(key: str, args: Mapping[str, str], locale: str) -> str:
    """The sentence of an error: a copy key, or the message of `errors.<key>`."""
    message = text(key if "." in key else f"errors.{key}.message", locale)
    for name, value in args.items():
        message = message.replace("{" + name + "}", value)
    return message


def text(key: str, locale: str) -> str:
    """The copy under `key` for `locale`, English when the key is missing there."""
    try:
        return cast(
            str, t(key, locale=normalize_locale(locale), default=t(key, locale="en-us"))
        )
    except Exception:
        return cast(str, t(key, locale="en-us"))

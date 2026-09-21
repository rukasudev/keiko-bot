"""Registered value styles: how a stored value reads in a summary or a panel."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.settings.form.copy import text
from app.settings.form.responses.dates import MONTH_KEYS

STYLES: tuple[str, ...] = (
    "channel",
    "role",
    "user",
    "bullet",
    "numbered",
    "code",
    "boolean",
    "boolean-mode",
    "mm_dd",
    "composition",
)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "não", "nao", "0", "")
    return bool(value)


def format_boolean(value: Any, locale: str) -> str:
    """Yes or no in the locale's words."""
    key = "buttons.yes.label" if _truthy(value) else "buttons.no.label"
    return text(key, locale)


def month_label(month: int, locale: str) -> str | None:
    """The month name in the locale, None for a number outside 1 to 12."""
    if not 1 <= month <= 12:
        return None
    return text(f"commands.commands.commons.months.{MONTH_KEYS[month - 1]}", locale)


def format_mm_dd(value: Any, locale: str) -> str:
    """`12 de maio` or `May 12` from a stored `MM-DD`."""
    try:
        month, day = (int(part) for part in str(value).split("-"))
    except (TypeError, ValueError):
        return str(value)
    label = month_label(month, locale)
    if not label:
        return str(value)
    if locale == "pt-br":
        return f"{day} de {label.lower()}"
    return f"{label} {day}"


def _lines(items: Sequence[str], style: str) -> str:
    if not items:
        return ""
    if style == "bullet":
        body = "\n".join(f"• {item}" for item in items)
    elif style == "numbered":
        body = "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))
    else:
        body = "\n".join(str(item) for item in items)
    return f"\n```{body}```"


def format_single(value: Any, style: str | None, locale: str) -> str:
    """One scalar value in `style`."""
    if style == "boolean-mode":
        return format_boolean(value == "custom", locale)
    if value in ("default", "custom"):
        return text(f"buttons.summary-card.{value}-label", locale) or str(value)
    if style == "boolean":
        return format_boolean(value, locale)
    if style == "mm_dd":
        return format_mm_dd(value, locale)
    if value is None:
        return empty_value(locale)
    mentions: dict[str, Callable[[Any], str]] = {
        "channel": lambda value: f"<#{value}>",
        "role": lambda value: f"<@&{value}>",
        "user": lambda value: f"<@{value}>",
        "code": lambda value: f"`{value}`",
    }
    if style in mentions:
        return mentions[style](value)
    if style in ("bullet", "numbered"):
        return _lines([part.lstrip() for part in str(value).split(";")], style)
    return str(value)


def format_list(values: Sequence[Any], style: str | None) -> str:
    """A list of values in `style`."""
    mentions = {"channel": "<#{}>", "role": "<@&{}>", "user": "<@{}>"}
    if style in mentions:
        return ", ".join(mentions[style].format(value) for value in values)
    if style in ("code", "bullet", "numbered"):
        return _lines([str(value) for value in values], style)
    return ", ".join(str(value) for value in values)


def empty_value(locale: str) -> str:
    """What a setting with nothing in it reads as, wherever it is shown."""
    return text("commands.resume.empty", locale) or "-"


def format_value(value: Any, style: str | None, locale: str) -> str:
    """A stored value as the user reads it, scalar or list."""
    if isinstance(value, (str, bool, int, float)) or value is None:
        return format_single(value, style, locale)
    return format_list(list(value), style)

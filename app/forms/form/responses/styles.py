"""Registered value styles: how a stored value reads in a summary or a panel.

Saved documents keep the style spellings released before a form's `format`
values became snake_case, so a declared format goes through `stored_style`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.core.calendar import mm_dd_label
from app.forms.form.copy import text

STYLES: tuple[str, ...] = (
    "channel",
    "role",
    "user",
    "bullet",
    "code-block",
    "numbered",
    "code",
    "boolean",
    "boolean-mode",
    "mm_dd",
    "composition",
)
SAVED_AS: dict[str, str] = {
    "code_block": "code-block",
    "month_day": "mm_dd",
    "customized": "boolean-mode",
}


def stored_style(declared: str | None) -> str | None:
    """The style a form's declared `format` reads and is saved in, as released."""
    return SAVED_AS.get(declared, declared) if declared else None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "não", "nao", "0", "")
    return bool(value)


def format_boolean(value: Any, locale: str) -> str:
    """Yes or no in the locale's words."""
    key = "texts.boolean.yes" if _truthy(value) else "texts.boolean.no"
    return text(key, locale)


def format_mm_dd(value: Any, locale: str) -> str:
    """`12 de maio` or `May 12` from a stored `MM-DD`."""
    return mm_dd_label(str(value), lambda key: text(key, locale))


def _lines(items: Sequence[str], style: str) -> str:
    if not items:
        return ""
    if style == "code-block":
        return "\n```" + "\n".join(f"• {item}" for item in items) + "```"
    if style == "numbered":
        return "".join(f"\n{index}. {item}" for index, item in enumerate(items, 1))
    if style == "code":
        return "".join(f"\n- `{item}`" for item in items)
    return "".join(f"\n- {item}" for item in items)


def format_single(value: Any, style: str | None, locale: str) -> str:
    """One scalar value in `style`."""
    if style == "boolean-mode":
        return format_boolean(value == "custom", locale)
    if value in ("default", "custom"):
        return text(f"texts.modes.{value}", locale) or str(value)
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
    if style in ("bullet", "code-block", "numbered"):
        return _lines([part.lstrip() for part in str(value).split(";")], style)
    return str(value)


def format_list(values: Sequence[Any], style: str | None) -> str:
    """A list of values in `style`."""
    mentions = {"channel": "<#{}>", "role": "<@&{}>", "user": "<@{}>"}
    if style in mentions:
        return ", ".join(mentions[style].format(value) for value in values)
    if style in ("code", "bullet", "code-block", "numbered"):
        return _lines([str(value) for value in values], style)
    return ", ".join(str(value) for value in values)


def empty_value(locale: str) -> str:
    """What a setting with nothing in it reads as, wherever it is shown."""
    return text("texts.empty_value", locale) or "-"


def format_value(value: Any, style: str | None, locale: str) -> str:
    """A stored value as the user reads it, scalar or list."""
    if isinstance(value, (str, bool, int, float)) or value is None:
        return format_single(value, style, locale)
    return format_list(list(value), style)

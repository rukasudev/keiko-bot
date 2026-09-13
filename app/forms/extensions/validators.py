"""Registered validators: pure checks that name the data they need.

A validator returns the error key under `errors.` that explains the refusal,
or None when the value is fine. Whatever it needs from outside the session
(an external lookup, the feature's saved items) arrives in the context the
adapter prefetched, so the check itself never waits on anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.forms.extensions.dates import parse_date_parts
from app.forms.extensions.links import parse_link


@dataclass(frozen=True)
class ValidationContext:
    """Everything a validator may look at besides the value."""

    answers: Mapping[str, Any] = field(default_factory=dict)
    items: Sequence[Mapping[str, Any]] = ()
    external: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


Check = Callable[[Any, ValidationContext], "str | None"]


@dataclass(frozen=True)
class Validator:
    """A named check and the context it needs prefetched."""

    name: str
    needs: tuple[str, ...]
    check: Check


def _registered(items: Sequence[Mapping[str, Any]], key: str, value: str) -> bool:
    for item in items:
        stored = item.get(key)
        if isinstance(stored, Mapping):
            stored = stored.get("value")
        if isinstance(stored, str) and stored.lower() == value.lower():
            return True
    return False


def _streamer_name(value: Any, context: ValidationContext) -> str | None:
    name = str(value or "")
    if _registered(context.items, "streamer", name):
        return "streamer-already-registered"
    if context.external.get("twitch", {}).get("user_id") is None:
        return "streamer-not-found"
    return None


def _youtube_channel(value: Any, context: ValidationContext) -> str | None:
    name = str(value or "")
    if _registered(context.items, "youtuber", name):
        return "youtuber-already-registered"
    if context.external.get("youtube", {}).get("channel_id") is None:
        return "youtuber-not-found"
    return None


def _link_or_domain(value: Any, _context: ValidationContext) -> str | None:
    text = str(value or "").strip()
    if not text or " " in text:
        return "link-not-recognized"
    host = parse_link(text).host
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    if host and tld.isalpha() and len(tld) >= 2:
        return None
    return "link-not-recognized"


def _date(value: Any, context: ValidationContext) -> str | None:
    if isinstance(value, Mapping):
        day = value.get("day")
        month = value.get("month") or context.answers.get("month")
    else:
        day, month = value, context.answers.get("month")
    return None if parse_date_parts(day, month) else "invalid-date"


VALIDATORS: dict[str, Validator] = {
    "validate_streamer_name": Validator(
        "validate_streamer_name", ("external:twitch", "items"), _streamer_name
    ),
    "validate_youtube_channel": Validator(
        "validate_youtube_channel", ("external:youtube", "items"), _youtube_channel
    ),
    "validate_link_or_domain": Validator(
        "validate_link_or_domain", (), _link_or_domain
    ),
    "validate_date": Validator("validate_date", ("answers",), _date),
}


def validator(name: str | None) -> Validator | None:
    """The registered validator called `name`, None when there is none."""
    return VALIDATORS.get(name) if name else None

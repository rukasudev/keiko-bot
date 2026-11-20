"""Registered validators: pure checks that name the data they need.

A validator returns the error key under `errors.` that explains the refusal,
or None when the value is fine. Whatever it needs from outside the session
(an external lookup, the feature's saved items) arrives in the context the
adapter prefetched, so the check itself never waits on anything, and the
parameters its declaration gives (`params`) arrive in the same context.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.constants import DBConfigs
from app.core.calendar import is_day, parse_date_parts
from app.forms.form.responses.links import parse_link


@dataclass(frozen=True)
class Upload:
    """What Discord tells of an uploaded file before anything downloads it."""

    size: int
    width: int | None
    height: int | None
    content_type: str | None


@dataclass(frozen=True)
class ValidationContext:
    """Everything a validator may look at besides the value."""

    answers: Mapping[str, Any] = field(default_factory=dict)
    items: Sequence[Mapping[str, Any]] = ()
    external: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    params: Mapping[str, str] = field(default_factory=dict)
    upload: Upload | None = None


def uploaded(reported: Any) -> Upload | None:
    """The upload a submitted modal reported, None when it reported none."""
    if not isinstance(reported, Mapping):
        return None
    width, height = reported.get("width"), reported.get("height")
    return Upload(
        size=int(reported.get("size") or 0),
        width=int(width) if width is not None else None,
        height=int(height) if height is not None else None,
        content_type=reported.get("content_type"),
    )


Check = Callable[[Any, ValidationContext], "str | None"]


@dataclass(frozen=True)
class Validator:
    """A named check, the context it needs prefetched and the params it takes."""

    name: str
    needs: tuple[str, ...]
    check: Check
    params: tuple[str, ...] = ()

    def needed(self, params: Mapping[str, str]) -> tuple[str, ...]:
        """The needs once a declaration's `params` fill their `{param}` names."""
        return tuple(need.format_map(params) for need in self.needs)


def _registered(items: Sequence[Mapping[str, Any]], key: str, value: str) -> bool:
    for item in items:
        stored = item.get(key)
        if isinstance(stored, Mapping):
            stored = stored.get("value")
        if isinstance(stored, str) and stored.lower() == value.lower():
            return True
    return False


def _registered_and_found(value: Any, context: ValidationContext) -> str | None:
    params = context.params
    name = str(value or "")
    if _registered(context.items, params["item"], name):
        return f"{params['error']}_already_registered"
    if context.external.get(params["service"], {}).get(params["id"]) is None:
        return f"{params['error']}_not_found"
    return None


def _named(prefix: str, outcome: str) -> str:
    joint = "_" if "." in prefix else "-"
    return f"{prefix}{joint}{outcome.replace('_', joint)}"


def _link_or_domain(value: Any, _context: ValidationContext) -> str | None:
    text = str(value or "").strip()
    if not text or " " in text:
        return "link_not_recognized"
    host = parse_link(text).host
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    if host and tld.isalpha() and len(tld) >= 2:
        return None
    return "link_not_recognized"


def _date(value: Any, context: ValidationContext) -> str | None:
    month = context.answers.get(context.params["month"])
    fits = is_day(value) if month in (None, "") else parse_date_parts(value, month)
    return None if fits else "invalid_date"


def _image(_value: Any, context: ValidationContext) -> str | None:
    upload = context.upload
    accepted = {f"image/{name.lower()}" for name in DBConfigs.IMAGE_FORMATS}
    fits = (
        upload is not None
        and upload.width is not None
        and upload.height is not None
        and upload.size <= DBConfigs.IMAGE_MAX_BYTES
        and upload.width * upload.height <= DBConfigs.IMAGE_MAX_PIXELS
        and (upload.content_type or "").lower() in accepted
    )
    return None if fits else "image_not_accepted"


VALIDATORS: dict[str, Validator] = {
    "registered_and_found": Validator(
        "registered_and_found",
        ("external:{service}", "items"),
        _registered_and_found,
        ("item", "service", "id", "error"),
    ),
    "link_or_domain": Validator("link_or_domain", (), _link_or_domain),
    "date": Validator("date", ("answers",), _date, ("month",)),
    "image": Validator("image", ("upload",), _image),
}


def validator(name: str | None) -> Validator | None:
    """The registered validator called `name`, None when there is none."""
    return VALIDATORS.get(name) if name else None

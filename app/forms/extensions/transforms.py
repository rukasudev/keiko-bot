"""Registered transforms: how multi-part answers serialize and hydrate."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.forms.extensions.dates import parse_date_parts, split_mm_dd
from app.forms.extensions.links import normalize_link


@dataclass(frozen=True)
class Transform:
    """A named pair of functions between answer parts and the stored value."""

    name: str
    part_keys: tuple[str, ...]
    value_key: str
    style: str
    serialize: Callable[[Mapping[str, Any]], Any]
    hydrate: Callable[[Any], dict[str, Any]]


def _mm_dd_serialize(parts: Mapping[str, Any]) -> str | None:
    return parse_date_parts(parts.get("day"), parts.get("month"))


def _link_serialize(parts: Mapping[str, Any]) -> Any:
    raw = parts.get("link")
    return normalize_link(str(raw)) if raw else raw


def _link_hydrate(value: Any) -> dict[str, Any]:
    return {"link": value} if value else {}


TRANSFORMS: dict[str, Transform] = {
    "mm_dd_date_parts": Transform(
        name="mm_dd_date_parts",
        part_keys=("day", "month"),
        value_key="date",
        style="mm_dd",
        serialize=_mm_dd_serialize,
        hydrate=split_mm_dd,
    ),
    "normalize_link": Transform(
        name="normalize_link",
        part_keys=("link",),
        value_key="link",
        style="code",
        serialize=_link_serialize,
        hydrate=_link_hydrate,
    ),
}


def transform(name: str | None) -> Transform | None:
    """The registered transform called `name`, None when there is none."""
    return TRANSFORMS.get(name) if name else None

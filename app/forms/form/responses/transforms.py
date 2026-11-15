"""Registered transforms: how multi-part answers serialize and hydrate."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.core.calendar import parse_date_parts, split_mm_dd
from app.forms.form.responses.links import normalize_link


@dataclass(frozen=True)
class Transform:
    """A named pair of functions between answer parts and the stored value."""

    name: str
    part_keys: tuple[str, ...]
    value_key: str
    style: str
    serialize: Callable[[Mapping[str, Any]], Any]
    hydrate: Callable[[Any], dict[str, Any]]

    def read(self, answers: Mapping[str, Any]) -> Any:
        """The stored value of the answers that hold its parts."""
        return self.serialize({part: answers.get(part) for part in self.part_keys})

    def split(self, value: Any) -> dict[str, Any]:
        """The parts a stored value seeds back."""
        return self.hydrate(value)


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


def handle(value: str) -> str:
    """A typed nick or handle without spaces around it, a leading @ or capitals."""
    return value.strip().removeprefix("@").strip().lower()


def lowercase(value: str) -> str:
    """A typed value in lower case, as typed otherwise."""
    return value.lower()


NORMALIZERS: dict[str, Callable[[str], str]] = {
    "handle": handle,
    "lowercase": lowercase,
}


def normalizer(name: str | None) -> Callable[[str], str] | None:
    """The registered input normalizer called `name`, None when there is none."""
    return NORMALIZERS.get(name) if name else None

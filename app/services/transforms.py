"""Registry for YAML `response_transform` values.

Each transform declares how per-part step responses serialize into the stored
value and how a stored value hydrates back into its parts.
Reference: docs/form-configuration.md
"""
from typing import Any, Dict, Optional

from app.services.dates import parse_date_parts


def _mm_dd_serialize(parts: Dict[str, Any]) -> Optional[str]:
    return parse_date_parts(parts.get("day"), parts.get("month"))


def _mm_dd_hydrate(value: Any) -> Dict[str, str]:
    if not isinstance(value, str) or "-" not in value:
        return {}
    month, day = value.split("-", 1)
    try:
        return {"month": month, "day": str(int(day))}
    except ValueError:
        return {}


def _normalize_link_serialize(parts: Dict[str, Any]) -> Optional[str]:
    from urllib.parse import urlencode

    from app.services.block_links import parse_link

    raw = parts.get("link")
    if not raw:
        return raw
    parsed = parse_link(raw)
    normalized = parsed.host + parsed.path
    if parsed.query:
        normalized += f"?{urlencode(parsed.query)}"
    return normalized or raw


def _normalize_link_hydrate(value: Any) -> Dict[str, Any]:
    return {"link": value} if value else {}


RESPONSE_TRANSFORMS: Dict[str, Dict[str, Any]] = {
    "mm_dd_date_parts": {
        "part_keys": ["day", "month"],
        "value_key": "date",
        "style": "mm_dd",
        "serialize": _mm_dd_serialize,
        "hydrate": _mm_dd_hydrate,
    },
    "normalize_link": {
        "part_keys": ["link"],
        "value_key": "link",
        "style": "code",
        "serialize": _normalize_link_serialize,
        "hydrate": _normalize_link_hydrate,
    },
}


def get_response_transform(name: Any) -> Optional[Dict[str, Any]]:
    return RESPONSE_TRANSFORMS.get(name)

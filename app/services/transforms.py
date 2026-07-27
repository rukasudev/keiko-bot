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


RESPONSE_TRANSFORMS: Dict[str, Dict[str, Any]] = {
    "mm_dd_date_parts": {
        "part_keys": ["day", "month"],
        "value_key": "date",
        "style": "mm_dd",
        "serialize": _mm_dd_serialize,
        "hydrate": _mm_dd_hydrate,
    },
}


def get_response_transform(name: Any) -> Optional[Dict[str, Any]]:
    return RESPONSE_TRANSFORMS.get(name)

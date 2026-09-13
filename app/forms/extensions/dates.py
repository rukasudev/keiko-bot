"""Month and day handling for the mm-dd date the birthday form stores."""

from __future__ import annotations

import calendar
import re
import unicodedata
from typing import Any

MONTH_KEYS: tuple[str, ...] = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)

MM_DD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "janeiro": 1,
    "feb": 2,
    "february": 2,
    "fevereiro": 2,
    "mar": 3,
    "march": 3,
    "marco": 3,
    "apr": 4,
    "april": 4,
    "abril": 4,
    "may": 5,
    "maio": 5,
    "jun": 6,
    "june": 6,
    "junho": 6,
    "jul": 7,
    "july": 7,
    "julho": 7,
    "aug": 8,
    "august": 8,
    "agosto": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "setembro": 9,
    "oct": 10,
    "october": 10,
    "outubro": 10,
    "nov": 11,
    "november": 11,
    "novembro": 11,
    "dec": 12,
    "december": 12,
    "dezembro": 12,
}


def parse_month(value: Any) -> int | None:
    """A month number from a number or a name in English or Portuguese."""
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        month = int(raw)
        return month if 1 <= month <= 12 else None
    normalized = unicodedata.normalize("NFKD", raw)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return MONTH_NAMES.get(normalized.rstrip("."))


def is_valid_mm_dd(value: str) -> bool:
    """True for a real calendar date in `MM-DD` form."""
    if not MM_DD.match(str(value)):
        return False
    month, day = (int(part) for part in str(value).split("-"))
    return day <= calendar.monthrange(2024, month)[1]


def parse_date_parts(day: Any, month: Any) -> str | None:
    """`MM-DD` from a day and a month, or None when they make no date."""
    try:
        day_number = int(str(day).strip())
    except (TypeError, ValueError):
        return None
    month_number = parse_month(month)
    if not month_number:
        return None
    mm_dd = f"{month_number:02d}-{day_number:02d}"
    return mm_dd if is_valid_mm_dd(mm_dd) else None


def split_mm_dd(value: Any) -> dict[str, str]:
    """`{"month", "day"}` from a stored `MM-DD`, empty when it is not one."""
    if not isinstance(value, str) or "-" not in value:
        return {}
    month, day = value.split("-", 1)
    try:
        return {"month": month, "day": str(int(day))}
    except ValueError:
        return {}

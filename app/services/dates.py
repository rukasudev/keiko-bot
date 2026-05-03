import calendar
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, List, Optional, Tuple

from discord import app_commands

from app.services.utils import ml
from app.translator import locale_str


MONTH_KEYS: List[str] = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]

MM_DD_REGEX = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

MONTH_NAMES_TO_NUMBER = {
    "jan": 1, "january": 1, "janeiro": 1,
    "feb": 2, "february": 2, "fevereiro": 2,
    "mar": 3, "march": 3, "marco": 3, "março": 3,
    "apr": 4, "april": 4, "abril": 4,
    "may": 5, "maio": 5,
    "jun": 6, "june": 6, "junho": 6,
    "jul": 7, "july": 7, "julho": 7,
    "aug": 8, "august": 8, "agosto": 8,
    "sep": 9, "sept": 9, "september": 9, "setembro": 9,
    "oct": 10, "october": 10, "outubro": 10,
    "nov": 11, "november": 11, "novembro": 11,
    "dec": 12, "december": 12, "dezembro": 12,
}


def get_month_label(month: int, locale: str = None) -> Optional[str]:
    if not 1 <= month <= 12:
        return None
    key = MONTH_KEYS[month - 1]
    return ml(f"commands.commands.commons.months.{key}", locale=locale)


def get_month_choices(namespace: str = "commons") -> List[app_commands.Choice[int]]:
    return [
        app_commands.Choice(
            name=locale_str(key, type=f"months.{key}", namespace=namespace),
            value=index,
        )
        for index, key in enumerate(MONTH_KEYS, start=1)
    ]


def is_valid_mm_dd(value: str) -> bool:
    if not MM_DD_REGEX.match(str(value)):
        return False
    month, day = parse_mm_dd(value)
    return day <= calendar.monthrange(2024, month)[1]


def parse_mm_dd(value: str) -> Tuple[int, int]:
    return int(value.split("-")[0]), int(value.split("-")[1])


def parse_month_name(value: Any) -> Optional[int]:
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        month = int(raw)
        return month if 1 <= month <= 12 else None

    normalized = unicodedata.normalize("NFKD", raw)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.rstrip(".")
    return MONTH_NAMES_TO_NUMBER.get(normalized)


def parse_date_parts(day: Any, month: Any) -> Optional[str]:
    try:
        day_number = int(str(day).strip())
    except (TypeError, ValueError):
        return None

    month_number = parse_month_name(month)
    if not month_number:
        return None

    mm_dd = f"{month_number:02d}-{day_number:02d}"
    return mm_dd if is_valid_mm_dd(mm_dd) else None


def next_mm_dd_occurrence(mm_dd: str, today: Optional[date] = None) -> datetime:
    today = today or datetime.now(timezone.utc).date()
    month, day = parse_mm_dd(mm_dd)
    candidate = _safe_date(today.year, month, day)
    if candidate <= today:
        candidate = _safe_date(today.year + 1, month, day)
    return datetime(candidate.year, candidate.month, candidate.day, 12, 0, 0, tzinfo=timezone.utc)


def _safe_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, day - 1)

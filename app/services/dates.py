from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

from discord import app_commands

from app.settings.form.responses.dates import MONTH_KEYS, is_valid_mm_dd, parse_date_parts
from app.services.utils import ml
from app.translator import locale_str

__all__ = [
    "MONTH_KEYS",
    "is_valid_mm_dd",
    "parse_date_parts",
    "get_month_label",
    "get_month_choices",
    "parse_mm_dd",
    "next_mm_dd_occurrence",
    "format_mm_dd_label",
    "format_month_count",
    "format_mm_dd_count",
]


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


def parse_mm_dd(value: str) -> Tuple[int, int]:
    return int(value.split("-")[0]), int(value.split("-")[1])


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


def format_mm_dd_label(value: str, locale: str = None) -> str:
    try:
        month, day = parse_mm_dd(str(value))
    except (TypeError, ValueError):
        return value

    label = get_month_label(month, locale)
    if not label:
        return value

    if str(locale).lower() == "pt-br":
        return f"{day} de {label.lower()}"
    return f"{label} {day}"


def format_month_count(value: Optional[Tuple[int, int]], locale: str = None) -> str:
    if not value:
        return "-"
    label = get_month_label(int(value[0]), locale) or str(value[0])
    return f"{label} ({value[1]})"


def format_mm_dd_count(value: Optional[Tuple[str, int]], locale: str = None) -> str:
    if not value:
        return "-"
    return f"{format_mm_dd_label(value[0], locale)} ({value[1]})"

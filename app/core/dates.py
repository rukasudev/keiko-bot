"""Calendar dates: the `MM-DD` a yearly date is stored as, month names and time zones."""

from __future__ import annotations

from datetime import date, datetime, timezone, tzinfo
from typing import Any, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands

from app import logger
from app.constants import LogTypes as logconstants
from app.core.calendar import MONTH_KEYS, mm_dd_label, month_key, parse_mm_dd
from app.core.utils import format_relative_time, ml
from app.translator import locale_str


def utc_today() -> date:
    """Today's date in UTC."""
    return datetime.now(timezone.utc).date()


def get_month_label(
    month: int, locale: str | discord.Locale | None = None
) -> Optional[str]:
    key = month_key(month)
    return ml(f"messages.months.{key}", locale=locale) if key else None


def get_month_choices(
    namespace: str = "messages",
) -> List[app_commands.Choice[int]]:
    return [
        app_commands.Choice(
            name=locale_str(key, type=f"months.{key}", namespace=namespace),
            value=index,
        )
        for index, key in enumerate(MONTH_KEYS, start=1)
    ]


def next_mm_dd_occurrence(mm_dd: str, today: Optional[date] = None) -> datetime:
    today = today or utc_today()
    month, day = parse_mm_dd(mm_dd)
    candidate = _safe_date(today.year, month, day)
    if candidate <= today:
        candidate = _safe_date(today.year + 1, month, day)
    return datetime(candidate.year, candidate.month, candidate.day, 12, 0, 0, tzinfo=timezone.utc)


def nearest_mm_dd_occurrence(mm_dd: str, today: date) -> date:
    """The `mm_dd` nearest to `today`, half a year either side at most, 02-29 being 02-28 in common years."""
    month, day = parse_mm_dd(mm_dd)
    candidates = [_safe_date(today.year + offset, month, day) for offset in (-1, 0, 1)]
    return min(candidates, key=lambda candidate: abs((candidate - today).days))


def _safe_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        return date(year, month, day - 1)


def zone_or_utc(name: Optional[str]) -> tzinfo:
    """The IANA time zone `name`, or UTC when it is missing or unknown."""
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warn(f"Unknown time zone {name}, using UTC", log_type=logconstants.COMMAND_WARN_TYPE)
        return timezone.utc


def format_mm_dd_label(value: str, locale: str | discord.Locale | None = None) -> str:
    return mm_dd_label(value, lambda key: ml(key, locale))


def parse_if_time(value: Any) -> Any:
    """A datetime as its date and how long ago it was; any other value as it is."""
    try:
        formatted = value.strftime("%Y-%m-%d %H:%M:%S")
        return f"{formatted} ({format_relative_time(value)})"
    except AttributeError:
        return value

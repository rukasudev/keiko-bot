"""Calendar dates: the `MM-DD` a yearly date is stored as, month names, time zones, and
the spans of time a member spends in a server."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands

from app import logger
from app.constants import LogTypes as logconstants
from app.core.calendar import (
    MONTH_KEYS,
    add_months,
    mm_dd_label,
    month_key,
    parse_mm_dd,
    safe_date,
    span_label,
)
from app.core.utils import fill, format_relative_time, ml
from app.translator import locale_str


def utc_now() -> datetime:
    """The moment it is now, in UTC."""
    return datetime.now(timezone.utc)


def utc_today() -> date:
    """Today's date in UTC."""
    return datetime.now(timezone.utc).date()


def get_month_label(
    month: int, locale: str | discord.Locale | None = None
) -> Optional[str]:
    key = month_key(month)
    return ml(f"texts.months.{key}", locale=locale) if key else None


def get_month_choices(
    namespace: str = "texts",
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
    candidate = safe_date(today.year, month, day)
    if candidate <= today:
        candidate = safe_date(today.year + 1, month, day)
    return datetime(candidate.year, candidate.month, candidate.day, 12, 0, 0, tzinfo=timezone.utc)


def nearest_mm_dd_occurrence(mm_dd: str, today: date) -> date:
    """The `mm_dd` nearest to `today`, half a year either side at most, 02-29 being 02-28 in common years."""
    month, day = parse_mm_dd(mm_dd)
    candidates = [safe_date(today.year + offset, month, day) for offset in (-1, 0, 1)]
    return min(candidates, key=lambda candidate: abs((candidate - today).days))


def elapsed_label(start: date, today: date, locale: Any) -> str:
    """How long since `start`, as a member's time in a server reads: its years and
    months once a month went by (`1 ano e 2 meses`), its weeks from two weeks on
    (`3 semanas`), its days before (`12 dias`)."""

    def copy(key: str) -> str:
        return ml(key, locale)

    months = _whole_months(start, today)
    if months:
        years, rest = divmod(months, 12)
        parts = [
            span_label(amount, unit, copy)
            for amount, unit in ((years, "years"), (rest, "months"))
            if amount
        ]
        return copy("texts.list_and").join(parts)
    days = max((today - start).days, 0)
    if days >= 14:
        return span_label(days // 7, "weeks", copy)
    return span_label(days, "days", copy)


def _whole_months(start: date, today: date) -> int:
    months = (today.year - start.year) * 12 + today.month - start.month
    if months > 0 and add_months(start, months) > today:
        months -= 1
    return max(months, 0)


def full_date_label(day: date, locale: Any) -> str:
    """A date with its year, `May 12, 2025` or `12/05/2025`."""
    key = month_key(day.month)
    return fill(
        ml("texts.full_date", locale),
        month_name=ml(f"texts.months.{key}", locale),
        day=day.day,
        year=day.year,
        day_padded=f"{day.day:02d}",
        month_padded=f"{day.month:02d}",
    )


def zone_or_utc(name: Optional[str]) -> tzinfo:
    """The IANA time zone `name`, or UTC when it is missing or unknown."""
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warn(f"Unknown time zone {name}, using UTC", log_type=logconstants.COMMAND_WARN_TYPE)
        return timezone.utc


def next_day_start(zone: tzinfo, now: datetime) -> datetime:
    """When the next day begins in `zone` after `now`, in UTC."""
    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0), tzinfo=zone).astimezone(timezone.utc)


def format_mm_dd_label(value: str, locale: str | discord.Locale | None = None) -> str:
    return mm_dd_label(value, lambda key: ml(key, locale))


def parse_if_time(value: Any) -> Any:
    """A datetime as its date and how long ago it was; any other value as it is."""
    try:
        formatted = value.strftime("%Y-%m-%d %H:%M:%S")
        return f"{formatted} ({format_relative_time(value)})"
    except AttributeError:
        return value

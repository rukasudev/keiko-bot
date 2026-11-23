"""The calendar a stored `MM-DD` and a stored time span live in, with the standard
library only."""

from __future__ import annotations

import calendar
import re
import unicodedata
from collections.abc import Callable
from datetime import date, timedelta
from string import Template
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
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
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


def is_day(value: Any) -> bool:
    """True for a day number some month has, whatever the month."""
    return parse_date_parts(value, 1) is not None


def split_mm_dd(value: Any) -> dict[str, str]:
    """`{"month", "day"}` from a stored `MM-DD`, empty when it is not one."""
    if not isinstance(value, str) or "-" not in value:
        return {}
    month, day = value.split("-", 1)
    try:
        return {"month": month, "day": str(int(day))}
    except ValueError:
        return {}


def parse_mm_dd(value: str) -> tuple[int, int]:
    """The month and the day of a stored `MM-DD`."""
    return int(value.split("-")[0]), int(value.split("-")[1])


def month_key(month: int) -> str | None:
    """The copy key suffix of a month number, None outside 1 to 12."""
    return MONTH_KEYS[month - 1] if 1 <= month <= 12 else None


def mm_dd_label(value: str, copy: Callable[[str], str]) -> str:
    """`May 12` or `12 de maio` from a stored `MM-DD`, its words read through `copy`."""
    try:
        month, day = parse_mm_dd(str(value))
    except (TypeError, ValueError, IndexError):
        return value
    key = month_key(month)
    label = copy(f"texts.months.{key}") if key else None
    if not label:
        return value
    return Template(copy("texts.date_label")).safe_substitute(
        month_name=label, month_lower=label.lower(), day=day
    )


SPAN = re.compile(r"^P([1-9]\d*)([DWMY])$")
UNIT_LETTERS = {"days": "D", "weeks": "W", "months": "M", "years": "Y"}
APPROXIMATE_DAYS = {"days": 1.0, "weeks": 7.0, "months": 30.436875, "years": 365.2425}


def time_span(amount: Any, unit: Any) -> str | None:
    """The span an amount of a unit is stored as, `P5M` for 5 months, or None when
    they make none."""
    try:
        number = int(str(amount).strip())
    except (TypeError, ValueError):
        return None
    letter = UNIT_LETTERS.get(str(unit))
    return f"P{number}{letter}" if letter and number > 0 else None


def span_parts(value: Any) -> tuple[int, str] | None:
    """The amount and the unit of a stored span, None when it is not one."""
    found = SPAN.match(str(value or ""))
    if found is None:
        return None
    units = {letter: unit for unit, letter in UNIT_LETTERS.items()}
    return int(found[1]), units[found[2]]


def split_span(value: Any) -> dict[str, Any]:
    """`{"amount", "unit"}` from a stored span, empty when it is not one."""
    parts = span_parts(value)
    return {"amount": parts[0], "unit": parts[1]} if parts else {}


def calendar_span(value: str) -> str:
    """A stored span as the calendar counts it, weeks in days and years in months, so
    `P4W` reads as `P28D`; any other value as it is."""
    parts = span_parts(value)
    if parts is None:
        return value
    amount, unit = parts
    if unit == "weeks":
        return f"P{amount * 7}D"
    if unit == "years":
        return f"P{amount * 12}M"
    return value


def span_days(value: Any) -> float:
    """About how many days a stored span lasts, to order spans of different units;
    zero for a value that is not one."""
    parts = span_parts(value)
    return parts[0] * APPROXIMATE_DAYS[parts[1]] if parts else 0.0


def span_fits(amount: int, unit: str, years: int) -> bool:
    """True when the span lasts no longer than `years` years from any day."""
    if unit in ("months", "years"):
        return amount * (12 if unit == "years" else 1) <= years * 12
    return amount * (7 if unit == "weeks" else 1) <= 365 * years + years // 4


def safe_date(year: int, month: int, day: int) -> date:
    """The date of `day` in the month, on its last day when the month is shorter."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def add_months(day: date, months: int) -> date:
    """`day` so many calendar months later, on the month's last day when it is
    shorter: Jan 31 and one month is Feb 28, or Feb 29 in a leap year."""
    index = day.year * 12 + day.month - 1 + months
    return safe_date(index // 12, index % 12 + 1, day.day)


def span_end(start: date, amount: int, unit: str) -> date:
    """The day a span that began on `start` is complete: so many calendar days, weeks,
    months or years later, a month too short for the day ending on its last day."""
    if unit == "days":
        return start + timedelta(days=amount)
    if unit == "weeks":
        return start + timedelta(weeks=amount)
    return add_months(start, amount * 12 if unit == "years" else amount)


def span_label(amount: int, unit: str, copy: Callable[[str], str]) -> str:
    """`5 meses` or `1 week` from an amount of a unit, its words read through
    `copy`."""
    table = "time_unit" if amount == 1 else "time_units"
    return Template(copy(f"texts.{table}.{unit}")).safe_substitute(count=amount)


def stored_span_label(value: Any, copy: Callable[[str], str]) -> str:
    """`5 meses` from a stored `P5M`, its words read through `copy`; any other value
    as it is."""
    parts = span_parts(value)
    return span_label(parts[0], parts[1], copy) if parts else str(value)

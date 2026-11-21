"""Dates a guild lives by: a stored `MM-DD` read as a date in each locale, the way every
screen shows it, its time zone, and the birthday a reminder celebrates."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.dates import (
    format_mm_dd_label,
    nearest_mm_dd_occurrence,
    parse_if_time,
    zone_or_utc,
)
from app.commands.birthdays.service import format_mm_dd_count, format_month_count
from app.forms.form.responses.styles import format_mm_dd

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value, locale, expected",
    [
        ("05-12", "en-us", "May 12"),
        ("05-12", "pt-br", "12 de maio"),
        ("12-01", "en-us", "December 1"),
        ("12-01", "pt-br", "1 de dezembro"),
        ("03-07", "pt-BR", "7 de março"),
    ],
)
def test_a_date_reads_the_same_in_core_and_in_the_form(value, locale, expected):
    assert format_mm_dd_label(value, locale) == expected
    assert format_mm_dd(value, locale.lower()) == expected


@pytest.mark.parametrize("value", ["", "13-01", "abc", "5"])
def test_a_value_that_is_no_date_reads_as_typed(value):
    assert format_mm_dd_label(value, "pt-br") == value
    assert format_mm_dd(value, "en-us") == value


def test_the_birthday_counts_name_the_month_and_the_day():
    assert format_month_count((5, 3), "en-us") == "May (3)"
    assert format_month_count((5, 3), "pt-br") == "Maio (3)"
    assert format_mm_dd_count(("05-12", 2), "pt-br") == "12 de maio (2)"
    assert format_month_count(None, "en-us") == format_mm_dd_count(None, "en-us") == "-"


def test_a_known_zone_is_the_zone_itself():
    assert zone_or_utc("America/Sao_Paulo") == ZoneInfo("America/Sao_Paulo")


@pytest.mark.parametrize("name", [None, "", "Mars/Olympus", "../etc/passwd"],
                         ids=["missing", "empty", "unknown", "malformed"])
def test_a_missing_or_unusable_zone_is_utc(name):
    assert zone_or_utc(name) is timezone.utc


@pytest.mark.parametrize("mm_dd, today, expected", [
    ("05-12", date(2026, 5, 12), date(2026, 5, 12)),
    ("05-12", date(2026, 9, 30), date(2026, 5, 12)),
    ("12-31", date(2027, 1, 1), date(2026, 12, 31)),
    ("12-31", date(2026, 12, 30), date(2026, 12, 31)),
    ("01-01", date(2026, 12, 31), date(2027, 1, 1)),
    ("02-29", date(2027, 2, 28), date(2027, 2, 28)),
    ("02-29", date(2028, 2, 29), date(2028, 2, 29)),
], ids=["on-the-day", "months-later", "late-after-new-year", "early-before-the-day",
        "early-before-new-year", "leap-day-in-a-common-year", "leap-day-in-a-leap-year"])
def test_the_birthday_celebrated_is_the_nearest_one_either_side_of_today(mm_dd, today, expected):
    assert nearest_mm_dd_occurrence(mm_dd, today) == expected


def test_a_time_reads_as_its_date_and_how_long_ago_and_anything_else_as_it_is():
    moment = datetime(2026, 5, 12, 9, 30, 0, tzinfo=timezone.utc)

    assert parse_if_time(moment).startswith("2026-05-12 09:30:00 (")
    assert parse_if_time("777") == "777"

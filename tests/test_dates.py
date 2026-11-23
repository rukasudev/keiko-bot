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


@pytest.mark.parametrize(
    "start, months, expected",
    [
        (date(2025, 1, 31), 1, date(2025, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),
        (date(2025, 1, 31), 2, date(2025, 3, 31)),
        (date(2025, 11, 15), 3, date(2026, 2, 15)),
    ],
)
def test_a_month_later_lands_on_the_last_day_of_a_shorter_month(start, months, expected):
    from app.core.calendar import add_months

    assert add_months(start, months) == expected


@pytest.mark.parametrize(
    "amount, unit, expected",
    [
        (7, "days", date(2025, 2, 7)),
        (3, "weeks", date(2025, 2, 21)),
        (1, "months", date(2025, 2, 28)),
        (1, "years", date(2026, 1, 31)),
    ],
)
def test_a_time_span_ends_on_the_calendar_day_it_reaches(amount, unit, expected):
    from app.core.calendar import span_end

    assert span_end(date(2025, 1, 31), amount, unit) == expected


@pytest.mark.parametrize(
    "value, locale, expected",
    [
        ("P7D", "pt-br", "7 dias"),
        ("P1D", "pt-br", "1 dia"),
        ("P1W", "pt-br", "1 semana"),
        ("P1M", "pt-br", "1 mês"),
        ("P14M", "pt-br", "14 meses"),
        ("P1Y", "pt-br", "1 ano"),
        ("P2Y", "pt-br", "2 anos"),
        ("P7D", "en-us", "7 days"),
        ("P1M", "en-us", "1 month"),
        ("P14M", "en-us", "14 months"),
        ("P2Y", "en-us", "2 years"),
        ("P1W", "en-us", "1 week"),
    ],
)
def test_a_time_span_reads_as_its_amount_and_unit(value, locale, expected):
    from app.forms.form.responses.styles import format_value

    assert format_value(value, "time_span", locale) == expected


@pytest.mark.parametrize(
    "joined, locale, expected",
    [
        (date(2026, 11, 15), "pt-br", "7 dias"),
        (date(2026, 11, 10), "pt-br", "12 dias"),
        (date(2026, 11, 1), "pt-br", "3 semanas"),
        (date(2026, 10, 22), "pt-br", "1 mês"),
        (date(2025, 9, 22), "pt-br", "1 ano e 2 meses"),
        (date(2024, 11, 22), "pt-br", "2 anos"),
        (date(2026, 11, 15), "en-us", "7 days"),
        (date(2026, 10, 22), "en-us", "1 month"),
        (date(2025, 9, 22), "en-us", "1 year and 2 months"),
        (date(2024, 11, 22), "en-us", "2 years"),
    ],
)
def test_a_members_time_in_the_server_reads_in_the_largest_whole_units(
    joined, locale, expected
):
    from app.core.dates import elapsed_label

    assert elapsed_label(joined, date(2026, 11, 22), locale) == expected


@pytest.mark.parametrize(
    "locale, expected", [("pt-br", "05/03/2025"), ("en-us", "March 5, 2025")]
)
def test_a_join_date_reads_with_its_year_in_each_locale(locale, expected):
    from app.core.dates import full_date_label

    assert full_date_label(date(2025, 3, 5), locale) == expected

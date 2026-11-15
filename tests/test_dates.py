"""A stored `MM-DD` read as a date in each locale, the way every screen shows it."""

import pytest

from app.core.dates import format_mm_dd_label
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

"""Dates a guild lives by: its time zone, and the birthday a reminder celebrates."""
from datetime import date, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.dates import nearest_mm_dd_occurrence, zone_or_utc

pytestmark = pytest.mark.unit


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

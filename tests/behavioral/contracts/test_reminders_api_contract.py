"""What Keiko sends to reminders-api.com, and why the shape matters.

The API takes the moment as three separate fields, documented as:

    date_tz   "Required - Date of the reminder in local timezone."   "2020-02-15"
    time_tz   "Required - Time of the reminder in local timezone."   "09:45"
    timezone  "Required - The timezone for the reminder."            "Europe/Helsinki"

Keiko never sent `time_tz`. When per-guild schedules shipped, the hour was put
inside `date_tz` as a tz-aware datetime instead, so the field received
`"2026-10-28 14:00:00+00:00"` where a date was expected and the API refused it.
Production shows the split cleanly: every guild without a timezone succeeded,
every guild with one failed, and four birthdays were stored pointing at no
reminder at all.

These tests pin the payload, because the failure is invisible from inside the
bot: the call returns a JSON body with no id, and nothing else goes wrong.
"""
import re

import pytest

from app.services import reminders as reminders_service

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("reminders_api")]

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLOCK = re.compile(r"^\d{2}:\d{2}$")


class FakeReminderAPI:
    """Records the payload and answers the way the real API does."""

    def __init__(self, response=None):
        self.created = []
        self.updated = []
        self._response = response if response is not None else {"id": 4242}

    def create_reminder(self, reminder_data):
        self.created.append(reminder_data)
        return self._response

    def update_reminder(self, reminder_id, date_tz, rrule=None, timezone=None, time_tz=None):
        self.updated.append({
            "reminder_id": reminder_id, "date_tz": date_tz,
            "rrule": rrule, "timezone": timezone, "time_tz": time_tz,
        })
        return {"id": reminder_id}


@pytest.fixture
def api(monkeypatch):
    fake = FakeReminderAPI()
    monkeypatch.setattr(reminders_service.bot, "reminder", fake, raising=False)
    monkeypatch.setattr(
        reminders_service.bot.config, "is_dev", lambda: False, raising=False
    )
    return fake


# --------------------------------------------------------------------------
# The payload contract
# --------------------------------------------------------------------------

def test_a_scheduled_reminder_sends_date_and_time_in_their_own_fields(api):
    """Broke as: the hour was folded into date_tz as an offset-aware datetime."""
    reminders_service.create_reminder(
        "birthday_reminder", "10-28", timezone_name="UTC", notification_time="14:00"
    )

    payload = api.created[0]
    assert ISO_DATE.match(str(payload["date_tz"])), payload["date_tz"]
    assert payload["time_tz"] == "14:00"
    assert payload["timezone"] == "UTC"


def test_date_tz_never_carries_a_clock_or_an_offset(api):
    """The two shapes the API rejected, pinned so they cannot come back."""
    for timezone_name, notification_time in (
        ("UTC", "14:00"),
        ("America/New_York", "12:00"),
        ("America/Sao_Paulo", None),
    ):
        api.created.clear()
        reminders_service.create_reminder(
            "birthday_reminder", "10-28",
            timezone_name=timezone_name, notification_time=notification_time,
        )
        sent = str(api.created[0]["date_tz"])
        assert "+" not in sent and ":" not in sent, sent
        assert ISO_DATE.match(sent), sent


def test_a_guild_without_a_schedule_still_sends_a_plain_date(api):
    """The shape that worked for thirteen production reminders."""
    reminders_service.create_reminder("birthday_reminder", "05-15")

    payload = api.created[0]
    assert ISO_DATE.match(str(payload["date_tz"]))
    assert payload.get("time_tz") in (None, "", "12:00")


def test_the_recurrence_rule_still_repeats_yearly_on_the_date(api):
    reminders_service.create_reminder(
        "birthday_reminder", "10-28", timezone_name="UTC", notification_time="14:00"
    )

    assert api.created[0]["rrule"] == "FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=28"


def test_february_29_still_falls_back_to_the_28th(api):
    reminders_service.create_reminder(
        "birthday_reminder", "02-29", timezone_name="UTC", notification_time="09:00"
    )

    assert "BYSETPOS=-1" in api.created[0]["rrule"]


def test_updating_a_reminder_uses_the_same_split(api):
    reminders_service.update_reminder(
        "4242", "10-28", timezone_name="UTC", notification_time="14:00"
    )

    sent = api.updated[0]
    assert ISO_DATE.match(str(sent["date_tz"])), sent["date_tz"]
    assert sent["time_tz"] == "14:00"


# --------------------------------------------------------------------------
# A refusal must be diagnosable and must never look like success
# --------------------------------------------------------------------------

def test_a_refused_creation_returns_nothing_and_says_why(monkeypatch, caplog):
    """The old log said only "Failed", which is why the cause took a database
    query and two years of archived files to find."""
    fake = FakeReminderAPI(response={"errors": {"date_tz": ["is not a valid date"]}})
    monkeypatch.setattr(reminders_service.bot, "reminder", fake, raising=False)
    monkeypatch.setattr(
        reminders_service.bot.config, "is_dev", lambda: False, raising=False
    )

    with caplog.at_level("ERROR"):
        result = reminders_service.create_reminder(
            "birthday_reminder", "10-28", timezone_name="UTC", notification_time="14:00"
        )

    assert result is None
    logged = caplog.text
    assert "date_tz" in logged, "the API's own complaint must reach the log"
    assert "is not a valid date" in logged


def test_a_transport_failure_is_reported_rather_than_raised(monkeypatch, caplog):
    class Exploding:
        def create_reminder(self, _data):
            raise ConnectionError("reminders-api unreachable")

    monkeypatch.setattr(reminders_service.bot, "reminder", Exploding(), raising=False)
    monkeypatch.setattr(
        reminders_service.bot.config, "is_dev", lambda: False, raising=False
    )

    with caplog.at_level("ERROR"):
        assert reminders_service.create_reminder("birthday_reminder", "10-28") is None

    assert "ConnectionError" in caplog.text

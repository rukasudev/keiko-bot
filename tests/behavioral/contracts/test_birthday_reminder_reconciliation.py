"""A birthday saved without a reminder must not stay that way.

Creating the reminder can fail: the API can refuse the payload, or simply be
unreachable while somebody is filling in a form. Losing the birthday over that
is worse than storing it and trying again, so the record is kept and a periodic
pass finishes the job.

This is also the repair path for what is already in production. Four birthdays
were stored pointing at no reminder between 2026-07-31 and 2026-08-08 and would
never have fired; the pass finds them by the same query it uses for new ones, so
no migration and no one-off script is needed for them.
"""
import pytest

from app.data import birthdays as birthdays_data
from app.services import reminders_birthdays as birthdays_service

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("birthday_reminders")]


@pytest.fixture
def guild_config():
    birthdays_data.upsert_birthday_config(
        "1532291713580925048",
        channel_id="99",
        timezone="UTC",
        notification_time="14:00",
    )
    return "1532291713580925048"


def orphan(guild_id, user_id, date):
    birthdays_data.upsert_birthday_item(guild_id, user_id, date, reminder_id=None)


def stored(guild_id, user_id):
    return birthdays_data.find_birthday_item(guild_id, user_id)


# --------------------------------------------------------------------------
# Finding what is broken
# --------------------------------------------------------------------------

def test_a_birthday_without_a_reminder_is_findable(guild_config):
    orphan(guild_config, "7", "10-28")
    birthdays_data.upsert_birthday_item(guild_config, "8", "05-15", reminder_id="4242")

    missing = birthdays_data.find_birthdays_missing_reminder()

    assert [item["user_id"] for item in missing] == ["7"]


def test_the_search_is_bounded_so_one_pass_cannot_run_away(guild_config):
    for index in range(10):
        orphan(guild_config, str(index), f"01-{index + 1:02d}")

    assert len(birthdays_data.find_birthdays_missing_reminder(limit=4)) == 4


# --------------------------------------------------------------------------
# Repairing it
# --------------------------------------------------------------------------

def test_the_pass_creates_the_missing_reminder_and_stores_it(guild_config, monkeypatch):
    orphan(guild_config, "7", "10-28")
    calls = []

    def create(title, mm_dd, notes=None, timezone_name=None, notification_time=None):
        calls.append((mm_dd, timezone_name, notification_time))
        return "5150"

    monkeypatch.setattr(birthdays_service.reminders_service, "create_reminder", create)

    repaired = birthdays_service.reconcile_missing_reminders()

    assert repaired == 1
    assert stored(guild_config, "7")["reminder_id"] == "5150"
    assert calls == [("10-28", "UTC", "14:00")]


def test_the_repair_uses_the_schedule_the_guild_configured(guild_config, monkeypatch):
    """The guild's timezone and hour are exactly what the original attempt got
    wrong, so the repair must read them again rather than assume a default."""
    orphan(guild_config, "7", "10-28")
    seen = {}

    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder",
        lambda *a, **kw: seen.update(kw) or "1",
    )

    birthdays_service.reconcile_missing_reminders()

    assert seen["timezone_name"] == "UTC"
    assert seen["notification_time"] == "14:00"


def test_everyone_sharing_a_date_gets_the_same_reminder(guild_config, monkeypatch):
    """One reminder serves a whole guild-and-date, which is why the id is
    written back to every record that shares it instead of once per person."""
    orphan(guild_config, "7", "10-28")
    orphan(guild_config, "8", "10-28")
    created = []

    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder",
        lambda *a, **kw: created.append(1) or "5150",
    )

    birthdays_service.reconcile_missing_reminders()

    assert len(created) == 1
    assert stored(guild_config, "7")["reminder_id"] == "5150"
    assert stored(guild_config, "8")["reminder_id"] == "5150"


def test_a_still_failing_reminder_leaves_the_birthday_alone(guild_config, monkeypatch):
    """The next pass tries again; nothing is lost and nothing is faked."""
    orphan(guild_config, "7", "10-28")
    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder", lambda *a, **kw: None
    )

    repaired = birthdays_service.reconcile_missing_reminders()

    assert repaired == 0
    assert stored(guild_config, "7")["reminder_id"] is None
    assert len(birthdays_data.find_birthdays_missing_reminder()) == 1


def test_one_broken_guild_does_not_stop_the_others(guild_config, monkeypatch):
    birthdays_data.upsert_birthday_config("other", channel_id="1")
    orphan(guild_config, "7", "10-28")
    orphan("other", "9", "03-23")

    def create(title, mm_dd, **kwargs):
        if mm_dd == "10-28":
            raise ConnectionError("api down")
        return "777"

    monkeypatch.setattr(birthdays_service.reminders_service, "create_reminder", create)

    assert birthdays_service.reconcile_missing_reminders() == 1
    assert stored("other", "9")["reminder_id"] == "777"


def test_a_guild_that_deleted_its_config_is_skipped(monkeypatch):
    orphan("gone", "7", "10-28")
    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder", lambda *a, **kw: "1"
    )

    # No config means no channel to greet in; creating a reminder for it would
    # schedule a message with nowhere to go.
    assert birthdays_service.reconcile_missing_reminders() == 0


# --------------------------------------------------------------------------
# The loop that runs it in production
# --------------------------------------------------------------------------

def run_loop(monkeypatch, outcome):
    """Drives the cog's task body without starting its loop."""
    import asyncio
    from types import SimpleNamespace

    from app.cogs.birthdays import Birthday

    monkeypatch.setattr(
        birthdays_service, "reconcile_missing_reminders", outcome
    )
    return asyncio.get_event_loop().run_until_complete(
        Birthday.reconcile_reminders.coro(SimpleNamespace(bot=SimpleNamespace()))
    )


def test_the_loop_reports_what_it_repaired(monkeypatch, caplog):
    with caplog.at_level("INFO"):
        run_loop(monkeypatch, lambda limit: 3)

    assert "3 birthday reminder" in caplog.text


def test_a_quiet_pass_says_nothing(monkeypatch, caplog):
    """Every thirty minutes forever: silence when there is nothing to do."""
    with caplog.at_level("INFO"):
        run_loop(monkeypatch, lambda limit: 0)

    assert "birthday reminder" not in caplog.text


def test_a_failing_pass_never_kills_the_loop(monkeypatch, caplog):
    """A task that raises stops rescheduling, and the repair would stop with it."""
    def explode(limit):
        raise ConnectionError("mongo is down")

    with caplog.at_level("WARNING"):
        assert run_loop(monkeypatch, explode) is None

    assert "mongo is down" in caplog.text

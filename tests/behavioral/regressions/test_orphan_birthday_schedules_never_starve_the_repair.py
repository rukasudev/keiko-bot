"""A birthday schedule nothing needs is released, so it never hides one that is needed.

What broke: the reconciliation pass reads at most `limit` birthday schedules without
a reminder and skipped, without releasing, the ones whose server had no birthday
configuration or no member on that date. Those came back on every pass, so once a
pass's worth of them piled up a real birthday behind them never got its reminder.
A birthday whose save failed after its date was scheduled left one more of them.

Shared behaviour: `reconcile_missing_reminders` and `upsert_birthday`
(`app/services/reminders_birthdays.py`) over `keiko.schedules`, run by the birthday
cog's loop and by `make repair-birthdays`. Consumer that exposed it: the review of
the new database.

Guaranteed: a pass releases every schedule it reads that nothing needs, a real
birthday behind more than one pass of them gets its reminder, and a save that fails
leaves no schedule behind.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.data import data, wait
from app.services import reminders_birthdays as birthdays_service
from app.services.reminders_birthdays import save_birthday_config
from tests.generators.birthdays import saved_birthday
from tests.generators.moderations import recorded_server

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("birthday_reminders")]

GUILD = "1532291713580925048"
LIMIT = 3
EARLY = datetime(2000, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def configured(deps):
    recorded_server(deps.mongo_client, GUILD, timezone="UTC")
    wait(save_birthday_config(GUILD, channel_id="99", notification_time="14:00"))
    return GUILD


@pytest.fixture
def creates(monkeypatch):
    created = []
    monkeypatch.setattr(
        birthdays_service.reminders_service,
        "create_reminder",
        lambda title, mm_dd, **kwargs: created.append(mm_dd) or f"r-{mm_dd}",
    )
    return created


def unneeded_schedule(guild_id, mm_dd, index):
    wait(data().keiko_schedules.upsert_schedule(
        birthdays_service.birthday_schedule_id(guild_id, mm_dd),
        "birthday",
        None,
        due_at=EARLY + timedelta(days=index),
    ))


def reminder_of(guild_id, mm_dd):
    schedule = wait(data().keiko_schedules.find_schedule(
        birthdays_service.birthday_schedule_id(guild_id, mm_dd)
    ))
    return (schedule or {}).get("reminder_id")


def test_a_real_birthday_behind_a_pass_of_unneeded_schedules_gets_its_reminder(
    configured, creates
):
    for index in range(LIMIT + 1):
        unneeded_schedule(configured, f"01-{index + 1:02d}", index)
    unneeded_schedule("no-config", "02-02", 0)
    saved_birthday(configured, "7", "12-25", reminder_id=None)
    wait(data().keiko_schedules.set_schedule_due(
        birthdays_service.birthday_schedule_id(configured, "12-25"),
        datetime(2100, 1, 1, tzinfo=timezone.utc),
    ))

    for _ in range(3):
        wait(birthdays_service.reconcile_missing_reminders(limit=LIMIT))

    assert reminder_of(configured, "12-25") == "r-12-25"
    assert creates == ["12-25"]
    assert wait(birthdays_service.birthday_schedules_missing_reminder(limit=50)) == []


def test_a_birthday_whose_save_fails_leaves_no_schedule(configured, creates, monkeypatch):
    async def refuse(*args, **kwargs):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(data().guild_members, "set_member_option", refuse)

    with pytest.raises(ConnectionError):
        wait(birthdays_service.upsert_birthday(configured, "7", "12-25"))

    assert wait(birthdays_service.find_birthday(configured, "7")) is None
    assert wait(data().keiko_schedules.find_schedule(
        birthdays_service.birthday_schedule_id(configured, "12-25")
    )) is None

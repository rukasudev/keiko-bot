"""Contract: every birthday's reminder, shared by its date and repaired when missing.

One schedule per server and date holds the reminder every birthday on it shares.
Decided in the database plan: `keiko.schedules` `birthday:{guild}:{MM-DD}` replaces the
`reminder_id` copied onto each birthday, so a date cannot get a second reminder by
construction. Before each server had its own reminder, one reminder served a date in every
server, and the migration keeps that id on each server's schedule.

A birthday saved without a reminder must not stay that way. Creating the reminder can
fail: the API can refuse the payload, or simply be unreachable while somebody is filling
in a form. Losing the birthday over that is worse than storing it and trying again, so the
record is kept and a periodic pass finishes the job. This is also the repair path for what
was already in production: four birthdays were stored pointing at no reminder between
2026-07-31 and 2026-08-08 and would never have fired; the pass finds them by the same query
it uses for new ones, so no migration and no one-off script was needed for them.

Guaranteed: members sharing a date share one reminder; the date's schedule and reminder go
with its last member; a reminder another server's schedule still uses is never deleted;
turning birthdays off forgets the birthdays and their schedules but keeps the year each
member was last celebrated, so turning them back on never celebrates twice in a year; and a
date left without a reminder gets one from a bounded pass, guild by guild, with the
schedule its guild configured, without ever killing the loop that runs it.
"""
import pytest

from app.data import data, wait
from app.commands.birthdays import service as birthdays_service
from app.commands.birthdays.service import (
    birthday_schedule_id,
    birthday_schedules_missing_reminder,
    find_birthday,
    save_birthday_config,
)
from tests.generators.birthdays import saved_birthday
from tests.generators.moderations import recorded_server

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("birthday_reminders")]


@pytest.fixture
def guild_config(deps):
    recorded_server(deps.mongo_client, "1532291713580925048", timezone="UTC")
    wait(save_birthday_config(
        "1532291713580925048",
        channel_id="99",
        notification_time="14:00",
    ))
    return "1532291713580925048"


def orphan(guild_id, user_id, date):
    saved_birthday(guild_id, user_id, date, reminder_id=None)


def stored(guild_id, user_id):
    """The schedule of the member's date: the reminder every birthday on it shares."""
    birthday = wait(find_birthday(guild_id, user_id))
    return wait(data().keiko_schedules.find_schedule(
        birthday_schedule_id(guild_id, birthday["date"])
    ))


def missing(limit=50):
    return wait(birthday_schedules_missing_reminder(limit))


# --------------------------------------------------------------------------
# Finding what is broken
# --------------------------------------------------------------------------

def test_a_birthday_without_a_reminder_is_findable(guild_config):
    orphan(guild_config, "7", "10-28")
    saved_birthday(guild_config, "8", "05-15", reminder_id="4242")

    assert [schedule["_id"] for schedule in missing()] == [
        birthday_schedule_id(guild_config, "10-28")
    ]


def test_the_search_is_bounded_so_one_pass_cannot_run_away(guild_config):
    for index in range(10):
        orphan(guild_config, str(index), f"01-{index + 1:02d}")

    assert len(missing(limit=4)) == 4


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

    repaired = wait(birthdays_service.reconcile_missing_reminders())

    assert repaired == 1
    assert stored(guild_config, "7")["reminder_id"] == "5150"
    assert calls == [("10-28", "UTC", "14:00")]


def test_the_repair_uses_the_schedule_the_guild_configured(guild_config, monkeypatch):
    """The server's timezone and the guild's hour are exactly what the original
    attempt got wrong, so the repair must read them again rather than assume a default."""
    orphan(guild_config, "7", "10-28")
    seen = {}

    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder",
        lambda *a, **kw: seen.update(kw) or "1",
    )

    wait(birthdays_service.reconcile_missing_reminders())

    assert seen["timezone_name"] == "UTC"
    assert seen["notification_time"] == "14:00"


def test_everyone_sharing_a_date_gets_the_same_reminder(guild_config, monkeypatch):
    """One reminder serves a whole guild-and-date: the date's schedule holds it once."""
    orphan(guild_config, "7", "10-28")
    orphan(guild_config, "8", "10-28")
    created = []

    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder",
        lambda *a, **kw: created.append(1) or "5150",
    )

    wait(birthdays_service.reconcile_missing_reminders())

    assert len(created) == 1
    assert stored(guild_config, "7")["reminder_id"] == "5150"
    assert stored(guild_config, "8")["reminder_id"] == "5150"


def test_a_still_failing_reminder_leaves_the_birthday_alone(guild_config, monkeypatch):
    """The next pass tries again; nothing is lost and nothing is faked."""
    orphan(guild_config, "7", "10-28")
    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder", lambda *a, **kw: None
    )

    repaired = wait(birthdays_service.reconcile_missing_reminders())

    assert repaired == 0
    assert stored(guild_config, "7")["reminder_id"] is None
    assert len(missing()) == 1


def test_one_broken_guild_does_not_stop_the_others(deps, guild_config, monkeypatch):
    recorded_server(deps.mongo_client, "other")
    wait(save_birthday_config("other", channel_id="1"))
    orphan(guild_config, "7", "10-28")
    orphan("other", "9", "03-23")

    def create(title, mm_dd, **kwargs):
        if mm_dd == "10-28":
            raise ConnectionError("api down")
        return "777"

    monkeypatch.setattr(birthdays_service.reminders_service, "create_reminder", create)

    assert wait(birthdays_service.reconcile_missing_reminders()) == 1
    assert stored("other", "9")["reminder_id"] == "777"


def test_a_guild_that_deleted_its_config_is_released(monkeypatch):
    """No config means no channel to greet in: a reminder for it would schedule a
    message with nowhere to go."""
    orphan("gone", "7", "10-28")
    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder", lambda *a, **kw: "1"
    )

    assert wait(birthdays_service.reconcile_missing_reminders()) == 0
    assert missing() == []


# --------------------------------------------------------------------------
# The loop that runs it in production
# --------------------------------------------------------------------------

def run_loop(monkeypatch, outcome):
    """Drives the cog's task body without starting its loop."""
    import asyncio
    from types import SimpleNamespace

    from app.commands.birthdays.cog import Birthday

    async def reconcile(limit):
        return outcome(limit)

    monkeypatch.setattr(birthdays_service, "reconcile_missing_reminders", reconcile)
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


# --------------------------------------------------------------------------
# One reminder per date
# --------------------------------------------------------------------------

@pytest.fixture
def api(deps, monkeypatch):
    calls = {"created": [], "deleted": []}

    def create(title, mm_dd, **kwargs):
        calls["created"].append(mm_dd)
        return f"r{len(calls['created'])}"

    monkeypatch.setattr(birthdays_service.reminders_service, "create_reminder", create)
    monkeypatch.setattr(
        birthdays_service.reminders_service, "delete_reminder", calls["deleted"].append
    )
    for guild_id in ("1", "2"):
        recorded_server(deps.mongo_client, guild_id, timezone="UTC")
        wait(save_birthday_config(guild_id, "9", False, notification_time="10:00"))
    return calls


def schedule(guild_id, mm_dd):
    return wait(data().keiko_schedules.find_schedule(birthday_schedule_id(guild_id, mm_dd)))


def test_members_sharing_a_date_share_one_reminder(api):
    wait(birthdays_service.upsert_birthday("1", "7", "05-12"))
    wait(birthdays_service.upsert_birthday("1", "8", "05-12"))

    assert api["created"] == ["05-12"]
    assert schedule("1", "05-12")["reminder_id"] == "r1"
    assert schedule("1", "05-12")["due_at"] is not None


def test_the_last_member_of_a_date_takes_its_schedule_and_reminder(api):
    wait(birthdays_service.upsert_birthday("1", "7", "05-12"))
    wait(birthdays_service.upsert_birthday("1", "8", "05-12"))

    wait(birthdays_service.remove_birthday("1", "7"))
    assert schedule("1", "05-12") is not None and api["deleted"] == []

    wait(birthdays_service.upsert_birthday("1", "8", "06-01"))
    assert schedule("1", "05-12") is None
    assert api["deleted"] == ["r1"]


def test_a_reminder_another_server_still_uses_is_kept(api):
    saved_birthday("1", "7", "05-12", reminder_id="shared")
    saved_birthday("2", "8", "05-12", reminder_id="shared")

    wait(birthdays_service.remove_birthday("1", "7"))

    assert schedule("1", "05-12") is None
    assert api["deleted"] == [], "the other server's birthday still needs it"

    wait(birthdays_service.remove_birthday("2", "8"))
    assert api["deleted"] == ["shared"]


def test_turning_birthdays_off_keeps_the_year_each_member_was_celebrated(api):
    wait(birthdays_service.upsert_birthday("1", "7", "05-12"))
    wait(birthdays_service.claim_birthday_year("1", "7", 2026))

    wait(birthdays_service.disable_birthdays("1"))

    assert wait(birthdays_service.find_birthdays_by_guild("1")) == []
    assert schedule("1", "05-12") is None and api["deleted"] == ["r1"]

    wait(birthdays_service.upsert_birthday("1", "7", "05-12"))
    assert wait(birthdays_service.claim_birthday_year("1", "7", 2026)) is False

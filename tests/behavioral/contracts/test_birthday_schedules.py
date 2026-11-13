"""One schedule per server and date holds the reminder every birthday on it shares.

Decided in the database plan: `keiko.schedules` `birthday:{guild}:{MM-DD}` replaces the
`reminder_id` copied onto each birthday, so a date cannot get a second reminder by
construction. Before each server had its own reminder, one reminder served a date in every
server, and the migration keeps that id on each server's schedule.

Guaranteed: members sharing a date share one reminder; the date's schedule and reminder go
with its last member; a reminder another server's schedule still uses is never deleted;
turning birthdays off forgets the birthdays and their schedules but keeps the year each
member was last celebrated, so turning them back on never celebrates twice in a year.
"""
import pytest

from app.services.reminders_birthdays import save_birthday_config
from app.data.keiko import schedules as schedules_data
from app.services import reminders_birthdays as birthdays_service
from tests.generators.birthdays import saved_birthday
from tests.generators.moderations import recorded_server

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("birthday_reminders")]


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
        save_birthday_config(guild_id, "9", False, notification_time="10:00")
    return calls


def schedule(guild_id, mm_dd):
    return schedules_data.find_schedule(birthdays_service.birthday_schedule_id(guild_id, mm_dd))


def test_members_sharing_a_date_share_one_reminder(api):
    birthdays_service.upsert_birthday("1", "7", "05-12")
    birthdays_service.upsert_birthday("1", "8", "05-12")

    assert api["created"] == ["05-12"]
    assert schedule("1", "05-12")["reminder_id"] == "r1"
    assert schedule("1", "05-12")["due_at"] is not None


def test_the_last_member_of_a_date_takes_its_schedule_and_reminder(api):
    birthdays_service.upsert_birthday("1", "7", "05-12")
    birthdays_service.upsert_birthday("1", "8", "05-12")

    birthdays_service.remove_birthday("1", "7")
    assert schedule("1", "05-12") is not None and api["deleted"] == []

    birthdays_service.upsert_birthday("1", "8", "06-01")
    assert schedule("1", "05-12") is None
    assert api["deleted"] == ["r1"]


def test_a_reminder_another_server_still_uses_is_kept(api):
    saved_birthday("1", "7", "05-12", reminder_id="shared")
    saved_birthday("2", "8", "05-12", reminder_id="shared")

    birthdays_service.remove_birthday("1", "7")

    assert schedule("1", "05-12") is None
    assert api["deleted"] == [], "the other server's birthday still needs it"

    birthdays_service.remove_birthday("2", "8")
    assert api["deleted"] == ["shared"]


def test_turning_birthdays_off_keeps_the_year_each_member_was_celebrated(api):
    birthdays_service.upsert_birthday("1", "7", "05-12")
    birthdays_service.claim_birthday_year("1", "7", 2026)

    birthdays_service.disable_birthdays("1")

    assert birthdays_service.find_birthdays_by_guild("1") == []
    assert schedule("1", "05-12") is None and api["deleted"] == ["r1"]

    birthdays_service.upsert_birthday("1", "7", "05-12")
    assert birthdays_service.claim_birthday_year("1", "7", 2026) is False

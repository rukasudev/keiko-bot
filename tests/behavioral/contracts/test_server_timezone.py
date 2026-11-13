"""A server keeps one time zone, and a new one applies to new reminders only.

Shared behaviour: `save_server_setting` (`app/core/servers.py`) for the key
`timezone`, which writes the setting and nothing else; the birthday card and the
birthday setup (`setup_birthdays`, `app/services/reminders_birthdays.py`), which read
the server's zone, make a zone chosen there the server's (the birthday configuration
holds no zone of its own since the database PR 1), and move every reminder only when
the hour changes, as before PR15; the readers that schedule and
celebrate a birthday, which ask `server_timezone`. Consumers: the settings screen of
/configurar, the birthday card, the birthday webhook, the missing reminders repair.

Broke as: PR15 moved every birthday reminder of a server when its zone changed, one
call to reminders-api.com per reminder at once, with a rollback when one refused.
Lucas chose to keep the reminders where they are (2026-11-08): the change is saved,
nothing is rescheduled, and the next birthday is created in the new zone.

Guaranteed: a new zone saved in the settings moves no reminder and leaves the birthday
configuration as it was, with no zone in it; the next birthday is scheduled in the new zone; a zone chosen
on the birthday card becomes the server's without moving a reminder; a new hour moves
each reminder once, in the server's zone; the card shows the server's zone; a
birthday's year is counted in the server's zone.
"""
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.constants import Commands
from app.core import servers as moderations_service
from app.core import reminders as reminders_service
from app.services import reminders_birthdays
from app.services.reminders_birthdays import birthday_config, save_birthday_config
from tests.generators.birthdays import last_celebrated_year, saved_birthday
from tests.generators.moderations import recorded_server
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("server_settings")]

GUILD = "777"
SAO_PAULO = "America/Sao_Paulo"
LISBON = "Europe/Lisbon"


class ReminderAPI:
    """reminders-api.com as Keiko calls it: each creation and update recorded."""

    def __init__(self):
        self.zones = {}
        self.created = []
        self.updates = []

    def update_reminder(self, reminder_id, date_tz, rrule=None, timezone=None, time_tz=None):
        self.updates.append((str(reminder_id), timezone, time_tz))
        self.zones[str(reminder_id)] = timezone
        return {"id": reminder_id}

    def create_reminder(self, reminder_data):
        self.created.append(reminder_data)
        return {"id": 4999}


@pytest.fixture
def api(monkeypatch):
    fake = ReminderAPI()
    monkeypatch.setattr(reminders_service.bot, "reminder", fake, raising=False)
    monkeypatch.setattr(reminders_service.bot.config, "is_dev", lambda: False, raising=False)
    return fake


@pytest.fixture
def birthdays(deps, api):
    """A server in São Paulo whose two birthdays are scheduled at 09:00 there."""
    recorded_server(deps.mongo_client, GUILD, timezone=SAO_PAULO)
    save_birthday_config(GUILD, "100", False, "09:00")
    saved_birthday(GUILD, "555", "05-12", reminder_id="4001")
    saved_birthday(GUILD, "556", "07-01", reminder_id="4002")
    api.zones.update({"4001": SAO_PAULO, "4002": SAO_PAULO})
    return api


def saved_zone(deps):
    document = deps.mongo_client.guild.settings.find_one({"_id": GUILD})
    return document["options"]["timezone"]


def test_a_new_zone_in_the_settings_moves_no_birthday(deps, birthdays):
    moderations_service.save_server_setting(GUILD, "timezone", LISBON)

    assert saved_zone(deps) == LISBON
    assert birthdays.updates == []
    assert birthdays.zones == {"4001": SAO_PAULO, "4002": SAO_PAULO}
    assert birthday_config(GUILD)["notification_time"] == "09:00"
    assert "timezone" not in birthday_config(GUILD)


def test_the_birthday_after_a_new_zone_is_scheduled_in_it(deps, birthdays):
    moderations_service.save_server_setting(GUILD, "timezone", LISBON)

    reminders_birthdays.upsert_birthday(GUILD, "557", "09-09")

    assert [reminder["timezone"] for reminder in birthdays.created] == [LISBON]
    assert birthdays.updates == []


def test_a_zone_saved_where_no_birthday_is_configured_writes_only_the_setting(deps, api):
    recorded_server(deps.mongo_client, GUILD, timezone=SAO_PAULO)

    moderations_service.save_server_setting(GUILD, "timezone", LISBON)

    assert saved_zone(deps) == LISBON
    assert birthday_config(GUILD) is None
    assert api.updates == []


def test_a_zone_chosen_on_the_birthday_card_is_the_servers_and_moves_nothing(
    deps, birthdays
):
    reminders_birthdays.save_birthday_config_changes(
        GUILD, {Commands.BIRTHDAY_CONFIG_TIMEZONE: {"values": [LISBON]}}
    )

    assert saved_zone(deps) == LISBON
    assert "timezone" not in birthday_config(GUILD)
    assert birthdays.updates == []


def test_a_new_hour_moves_each_birthday_once_in_the_servers_zone(deps, birthdays):
    recorded_server(deps.mongo_client, GUILD, timezone=LISBON)

    reminders_birthdays.save_birthday_config_changes(
        GUILD, {Commands.BIRTHDAY_CONFIG_NOTIFICATION_TIME: {"values": ["10:00"]}}
    )

    assert sorted(birthdays.updates) == [
        ("4001", LISBON, "10:00"),
        ("4002", LISBON, "10:00"),
    ]
    assert saved_zone(deps) == LISBON


def test_a_new_zone_and_hour_together_move_each_birthday_once(deps, birthdays):
    reminders_birthdays.setup_birthdays(GUILD, "100", False, LISBON, "10:00")

    assert sorted(birthdays.updates) == [
        ("4001", LISBON, "10:00"),
        ("4002", LISBON, "10:00"),
    ]
    assert saved_zone(deps) == LISBON
    assert birthday_config(GUILD)["notification_time"] == "10:00"


def test_the_birthday_card_shows_the_servers_zone(deps, birthdays):
    document = reminders_birthdays.birthday_manager_cog_data(GUILD)
    rows = reminders_birthdays.birthday_settings_rows(GUILD, "pt-br")

    assert document[Commands.BIRTHDAY_CONFIG_TIMEZONE] == SAO_PAULO
    assert next(row for row in rows if row["key"] == "timezone")["value"] == SAO_PAULO


def test_the_zone_chosen_in_the_birthday_setup_is_the_servers_zone(deps, api):
    recorded_server(deps.mongo_client, GUILD, timezone=SAO_PAULO)

    reminders_birthdays.setup_birthdays(GUILD, "100", False, LISBON, "09:00")

    assert saved_zone(deps) == LISBON
    assert "timezone" not in birthday_config(GUILD)


def test_the_not_applied_copy_and_error_are_gone():
    import app.exceptions as exceptions
    from app.core.utils import ml

    assert not hasattr(exceptions, "ServerSettingNotApplied")
    assert not hasattr(moderations_service, "register_setting_applier")
    for language in ("pt-br", "en-us"):
        key = "errors.server-setting-not-applied.timezone.title"
        assert ml(key, language) == key


@pytest.mark.parametrize(
    "server_zone, year", [("Asia/Tokyo", 2027), (SAO_PAULO, 2026)]
)
async def test_a_birthdays_year_is_counted_where_the_server_is(
    deps, monkeypatch, server_zone, year
):
    from app.webhooks import birthday_handler

    class Clock:
        moment = datetime(2026, 12, 31, 16, 0, tzinfo=ZoneInfo("UTC"))

        @classmethod
        def now(cls, tz=None):
            return cls.moment.astimezone(tz) if tz is not None else cls.moment

    guild = create_guild(id=int(GUILD), channels=["birthdays"])
    create_member(guild, id=555, name="Tester")
    deps.bot.get_guild = lambda guild_id: guild if str(guild_id) == GUILD else None
    monkeypatch.setattr(birthday_handler, "datetime", Clock)
    recorded_server(deps.mongo_client, GUILD, timezone=server_zone)
    save_birthday_config(GUILD, "100", False, "09:00")
    moderations_service.set_feature_enabled(GUILD, Commands.REMINDERS_BIRTHDAY_KEY, True)
    saved_birthday(GUILD, "555", "07-02", reminder_id="4001")

    await birthday_handler.celebrate_in_guild(
        GUILD, [{"user_id": "555", "date": "07-02"}], "07-02", Counter()
    )

    assert last_celebrated_year(GUILD, "555") == year

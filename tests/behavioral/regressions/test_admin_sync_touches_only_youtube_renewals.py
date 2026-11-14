"""The admin's reminder buttons act only on YouTube renewals.

Reported in the database plan (F6): `/admin subscriptions reminders` read every reminder on
the reminders API as a YouTube renewal, its notes as a youtuber. A birthday reminder's notes
are its date, which no server follows as a youtuber, so "Syncronize Reminders" deleted it;
and two servers celebrating on one date carry the same notes, so "Remove Duplicated
Reminders" deleted one of them as a duplicate. Either way a server's birthdays on that
date stopped being celebrated, with nothing in the log.

Shared behaviour: `get_reminder_subscriptions` (`app/services/subscriptions.py`) and the
two buttons of `app/components/buttons.py` it builds. Exposed by: the inventory of the
reminder rows for the move to `keiko.schedules`.

Guaranteed: a reminder is a YouTube renewal only when `keiko.schedules` says so (kind
`youtube_renewal`), or when it carries the renewal title and no schedule; a birthday
reminder is never listed, synchronized away or taken for a duplicate; the sync deletes a
renewal no server follows, with its schedule; the duplicates removed are the renewals
Keiko does not track, never the one its schedule points at.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.data import data, wait
from app.components.buttons import RemoveDuplicatedReminderButton, SyncronizeRemindersButton
from app.services import subscriptions
from app.services.notifications_youtube_video import youtube_renewal_id
from app.services.reminders_birthdays import birthday_schedule_id
from tests.generators.moderations import seed_cog

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

BIRTHDAY = "birthday_reminder"
RENEWAL = "youtube_notification"


class ReminderList:
    """reminders-api with a fixed list of reminders, recording each delete."""

    def __init__(self, reminders):
        self.reminders = reminders
        self.deleted = []

    def get_reminders(self):
        return {"data": self.reminders}

    def delete_reminder(self, reminder_id):
        self.deleted.append(str(reminder_id))
        return {}


@pytest.fixture
def api(deps, monkeypatch):
    reminders = ReminderList([
        {"id": 1, "title": BIRTHDAY, "notes": "05-12"},
        {"id": 2, "title": BIRTHDAY, "notes": "05-12"},
        {"id": 3, "title": RENEWAL, "notes": "pewdiepie"},
        {"id": 4, "title": RENEWAL, "notes": "mrbeast"},
        {"id": 5, "title": RENEWAL, "notes": "mrbeast"},
    ])
    deps.bot.reminder = reminders
    monkeypatch.setattr("app.bot", deps.bot)
    for guild_id, reminder_id in (("1001", "1"), ("1002", "2")):
        wait(data().keiko_schedules.upsert_schedule(
            birthday_schedule_id(guild_id, "05-12"), "birthday", reminder_id
        ))
    for youtuber, reminder_id in (("pewdiepie", "3"), ("mrbeast", "4")):
        wait(data().keiko_schedules.upsert_schedule(
            youtube_renewal_id(youtuber), "youtube_renewal", reminder_id
        ))
    seed_cog(deps.mongo_client, "notifications_youtube_video", {
        "guild_id": "1001",
        "enabled": True,
        "notifications": {"values": [{"youtuber": {"value": "mrbeast"}}]},
    })
    return reminders


def interaction():
    return SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def press(view, kind):
    [button] = [item for item in view.children if isinstance(item, kind)]
    await button.callback(interaction())


async def test_the_sync_deletes_only_a_renewal_no_server_follows(api):
    await press(await subscriptions.get_reminder_subscriptions(), SyncronizeRemindersButton)

    assert api.deleted == ["3"], "the birthdays of 05-12 stay scheduled"
    assert await data().keiko_schedules.find_schedule("youtube_renewal:pewdiepie") is None
    assert (await data().keiko_schedules.find_schedule("birthday:1001:05-12"))["reminder_id"] == "1"


async def test_two_servers_on_one_date_are_not_duplicates(api):
    await press(await subscriptions.get_reminder_subscriptions(), RemoveDuplicatedReminderButton)

    assert api.deleted == ["5"], "only the renewal Keiko does not track goes"


async def test_the_list_names_only_youtube_renewals(api):
    view = await subscriptions.get_reminder_subscriptions()

    names = [field.name for field in view.custom_embed.fields]
    assert names == ["pewdiepie (1)", "mrbeast (2)"]

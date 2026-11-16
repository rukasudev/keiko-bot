"""The admin's reminder buttons act only on YouTube renewals.

Reported in the database plan (F6): `/admin subscriptions reminders` read every reminder on
the reminders API as a YouTube renewal, its notes as a youtuber. A birthday reminder's notes
are its date, which no server follows as a youtuber, so "Syncronize Reminders" deleted it;
and two servers celebrating on one date carry the same notes, so "Remove Duplicated
Reminders" deleted one of them as a duplicate. Either way a server's birthdays on that
date stopped being celebrated, with nothing in the log.

Shared behaviour: `get_reminder_subscriptions` (`app/commands/admin/subscriptions.py`) and the
two buttons it builds there. Exposed by: the inventory of the
reminder rows for the move to `keiko.schedules`.

Guaranteed: a reminder is a YouTube renewal only when `keiko.schedules` says so (kind
`youtube_renewal`), or when it carries the renewal title and no schedule; a birthday
reminder is never listed, synchronized away or taken for a duplicate; the sync deletes a
renewal no server follows, with its schedule; the duplicates removed are the renewals
Keiko does not track, never the one its schedule points at; the Twitch sync drops only
the streamers no server follows; the three buttons keep their labels.

Found in the design review: admin kept copies of YouTube's rules (its storage path, its
renewal title and schedule kind, its count) to get around the ban on importing another
command, and reached Twitch's feature by key with an `isinstance`. Admin now reads both
through their `SubscriptionFeature`, and both lists still count only the servers that
announce while on, a paused server left out.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.data import data, wait
from app.commands.admin import subscriptions
from app.commands.youtube.service import youtube_renewal_id
from app.commands.birthdays.service import birthday_schedule_id
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
        locale="en-US",
        guild_id=None,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def press(view, label):
    [button] = [item for item in view.children if item.label == label]
    await button.callback(interaction())


async def test_the_sync_deletes_only_a_renewal_no_server_follows(api):
    await press(await subscriptions.get_reminder_subscriptions(), "Syncronize Reminders")

    assert api.deleted == ["3"], "the birthdays of 05-12 stay scheduled"
    assert await data().keiko_schedules.find_schedule("youtube_renewal:pewdiepie") is None
    assert (await data().keiko_schedules.find_schedule("birthday:1001:05-12"))["reminder_id"] == "1"


async def test_two_servers_on_one_date_are_not_duplicates(api):
    await press(await subscriptions.get_reminder_subscriptions(), "Remove Duplicated Reminders")

    assert api.deleted == ["5"], "only the renewal Keiko does not track goes"


async def test_the_list_names_only_youtube_renewals(api):
    view = await subscriptions.get_reminder_subscriptions()

    names = [field.name for field in view.custom_embed.fields]
    assert names == ["pewdiepie (1)", "mrbeast (2)"]


async def test_the_twitch_sync_unsubscribes_only_streamers_no_server_follows(monkeypatch):
    unsubscribed = []

    async def unsubscribe(_interaction, notification):
        unsubscribed.append(notification["streamer"]["value"])

    from app.core.commands import command_for

    twitch = command_for("notifications_twitch").feature
    monkeypatch.setattr(twitch, "unsubscribe", unsubscribe)
    view = subscriptions.generate_guilds_by_twitch_subscription_view(
        {"gaules": 2, "alanzoka": 0}
    )
    await press(view, "Syncronize Subscriptions")

    assert unsubscribed == ["alanzoka"]


async def test_the_admin_buttons_keep_their_labels():
    reminders = subscriptions.generate_reminders_by_streamer_view(
        subscriptions.ReminderOverview({}, {}, {}, {}, [])
    )
    twitch = subscriptions.generate_guilds_by_twitch_subscription_view({})

    assert [
        (button.label, str(button.emoji), button.style)
        for button in [*reminders.children, *twitch.children]
    ] == [
        ("Syncronize Reminders", "🔄", discord.ButtonStyle.grey),
        ("Remove Duplicated Reminders", "🗑️", discord.ButtonStyle.red),
        ("Syncronize Subscriptions", "🔄", discord.ButtonStyle.grey),
    ]


async def test_the_list_counts_only_the_servers_announcing_while_on(api, deps):
    seed_cog(deps.mongo_client, "notifications_youtube_video", {
        "guild_id": "1002",
        "enabled": False,
        "notifications": {"values": [{"youtuber": {"value": "mrbeast"}}]},
    })

    view = await subscriptions.get_reminder_subscriptions()

    counts = {field.name: field.value for field in view.custom_embed.fields}
    assert counts == {"pewdiepie (1)": "0", "mrbeast (2)": "1"}, "a paused server waits"


async def test_the_twitch_list_counts_the_servers_announcing_each_streamer(deps):
    seed_cog(deps.mongo_client, "notifications_twitch", {
        "guild_id": "1001",
        "enabled": True,
        "notifications": {"values": [{"streamer": {"value": "gaules"}}]},
    })
    seed_cog(deps.mongo_client, "notifications_twitch", {
        "guild_id": "1002",
        "enabled": False,
        "notifications": {"values": [{"streamer": {"value": "gaules"}}]},
    })
    deps.bot.twitch = SimpleNamespace(
        get_subscriptions=lambda: {"data": [
            {"condition": {"broadcaster_user_id": "1"}},
            {"condition": {"broadcaster_user_id": "2"}},
        ]},
        get_user_info_by_id=lambda user_id: {"login": {"1": "gaules", "2": "alanzoka"}[user_id]},
    )

    view = await subscriptions.get_twitch_subscriptions()

    counts = {field.name: field.value for field in view.custom_embed.fields}
    assert counts == {"gaules": "1", "alanzoka": "0"}


def test_only_a_command_that_keeps_subscriptions_has_a_subscription_feature():
    from app.core.commands import command_for
    from app.forms.features.subscriptions import subscription_feature

    for key in ("notifications_twitch", "notifications_youtube_video"):
        assert subscription_feature(key) is command_for(key).feature

    with pytest.raises(KeyError):
        subscription_feature("welcome_messages")

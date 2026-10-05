"""A youtuber only servers Keiko left still follow is let go: no more renewals.

Reported in the v1 review: leaving a server pauses its documents (`leave_guild`), and a
paused document counted as following in `count_servers_following`, on purpose, so an
unpaused server finds its youtuber still subscribed. A server Keiko left never unpauses,
so the renewal of a youtuber only such servers followed went on forever, every four
days, and the hub kept sending notices nobody would receive.

Shared behaviour: `count_servers_following` (`app/data/notifications_youtube_video.py`),
read by the renewal (`app/webhooks/reminder.py`) and by the unsubscribe of a youtuber a
server stops following. Exposed by: renewals of servers Keiko left in production.

Guaranteed: a server counts as following only while Keiko is in it (its moderations
record does not say it went offline); a youtuber nobody else follows drops its renewal
and is unsubscribed from the hub; a paused server Keiko is still in keeps its youtuber
renewed, as before.
"""
from types import SimpleNamespace

import pytest

from app.api import create_api
from app.constants import Commands
from app.data.reminder import find_reminder_by_id
from app.integrations.reminder_webhook import REMINDER_AUTH_USER
from app.services.notifications_youtube_video import unsubscribe_youtube_new_video
from tests.behavioral.regressions.test_reminder_webhook_trusts_only_the_reminders_api import (
    PASSWORD,
    RENEWAL_REMINDER,
    URL,
    RecordingReminders,
    basic,
    follow_youtuber,
    renewal,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]

LEFT = 4242
STAYED = 4343


@pytest.fixture
def keiko(deps):
    deps.bot.reminder = RecordingReminders()
    deps.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    deps.mongo_client.audit.reminders.insert_one(
        {"reminder_id": RENEWAL_REMINDER, "title": "youtube_notification", "value": "pewdiepie"}
    )
    return create_api("dev").test_client()


async def in_a_server_keiko_left(deps, guild_id=LEFT):
    """A server that followed the youtuber, then removed Keiko."""
    from app.cogs.events import Events

    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": str(guild_id),
        "is_bot_online": True,
        Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: True,
    })
    follow_youtuber(deps, str(guild_id), "pewdiepie")
    await Events(SimpleNamespace(user=SimpleNamespace(id=99), guilds=[])).on_guild_remove(
        SimpleNamespace(id=guild_id, owner=SimpleNamespace(id=313, mention="<@313>"))
    )


def notify_renewal(keiko):
    return keiko.post(
        URL,
        json={"reminders_notified": [renewal()]},
        headers=basic(REMINDER_AUTH_USER, PASSWORD),
    )


async def test_the_renewal_of_a_youtuber_only_servers_keiko_left_follow_ends(keiko, deps):
    await in_a_server_keiko_left(deps)

    response = notify_renewal(keiko)

    assert response.status_code == 200
    assert deps.bot.reminder.deleted == [RENEWAL_REMINDER]
    assert find_reminder_by_id(RENEWAL_REMINDER) is None
    assert deps.youtube.subscribe_calls == [], "nobody Keiko serves follows it any more"


async def test_a_server_keiko_is_still_in_keeps_the_youtuber_renewed(keiko, deps):
    await in_a_server_keiko_left(deps)
    follow_youtuber(deps, str(STAYED), "pewdiepie", enabled=False)

    response = notify_renewal(keiko)

    assert response.status_code == 200
    assert deps.bot.reminder.deleted == []
    assert deps.youtube.subscribe_calls == ["UC-pewdiepie"]


async def test_a_server_keiko_left_does_not_keep_a_youtuber_subscribed_for_another(
    keiko, deps
):
    await in_a_server_keiko_left(deps)
    follow_youtuber(deps, str(STAYED), "pewdiepie")

    unsubscribe_youtube_new_video(None, {"youtuber": {"value": "pewdiepie"}})

    assert deps.youtube.unsubscribe_calls == ["UC-pewdiepie"], (
        "the last server Keiko is in let it go, so the hub stops sending it"
    )

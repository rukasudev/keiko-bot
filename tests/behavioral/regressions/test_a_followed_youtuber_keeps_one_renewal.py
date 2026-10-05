"""A youtuber a server follows always has its renewal, and never two.

Reported in the v1 review of the loop and runtime PR: a youtuber followed only by a server
that removed Keiko loses its renewal, which drops itself once no server Keiko is in
follows it. When that server adds Keiko back and unpauses, nothing renewed it again: the
subscribe answered "already subscribed" by counting the servers that follow it, the
unpaused one among them, and the start's resubscribe created no renewal. A renewal row the
60-day expiry removed after renewals kept failing was lost the same way.

Shared behaviour: `subscribe_youtube_new_video` and `resubscribe_followed_channels`
(`app/services/notifications_youtube_video.py`), through `ensure_renewal`. Exposed by:
the review of the renewal that lets a youtuber go.

The next review found the renewal that ends itself counting the followers and deleting
its row outside the lock the subscribe takes, so a server adding the youtuber while that
renewal was deleting itself was told "already subscribed" and kept no renewal.

Guaranteed: "already subscribed" means a server follows the youtuber and its renewal row
exists; a followed youtuber without one gets one, from a server adding it or from the
start; however many ask at once, a youtuber has one renewal; and a renewal that ends
itself counts and deletes under the same lock, so a server adding the youtuber meanwhile
makes its own.
"""
import threading
import time
from types import SimpleNamespace

import pytest

from app.services import notifications_youtube_video
from tests.behavioral.regressions.test_reminder_webhook_trusts_only_the_reminders_api import (
    follow_youtuber,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]

RENEWAL = "youtube_notification"


class Reminders:
    """reminders-api, slow enough that two callers at once overlap, counting what it made."""

    def __init__(self):
        self.created = []
        self.deleted = []
        self.deleting = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self._lock = threading.Lock()

    def create_reminder(self, data):
        time.sleep(0.05)
        with self._lock:
            self.created.append(data["notes"])
            return {"id": 8000 + len(self.created)}

    def delete_reminder(self, reminder_id):
        self.deleting.set()
        self.release.wait(5)
        self.deleted.append(reminder_id)
        return {}


@pytest.fixture
def youtube(deps):
    deps.bot.reminder = Reminders()
    deps.bot.config.is_dev = lambda: False
    deps.bot.config.YOUTUBE_HUB_SECRET = "hub-secret"
    deps.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    deps.youtube.add_channel("UC-mrbeast", "MrBeast", custom_url="@mrbeast")
    return deps


def a_renewal(deps, reminder_id, youtuber):
    """The row a renewal reminder Keiko created keeps in `audit.reminders`."""
    deps.mongo_client.audit.reminders.insert_one(
        {"reminder_id": reminder_id, "title": RENEWAL, "value": youtuber}
    )


def renewals_of(deps, youtuber):
    return deps.mongo_client.audit.reminders.count_documents(
        {"title": RENEWAL, "value": youtuber}
    )


def a_server_adds(youtuber):
    notifications_youtube_video.subscribe_youtube_new_video(
        None, {"youtuber": {"value": youtuber}}
    )


async def test_a_followed_youtuber_without_a_renewal_gets_one_when_a_server_adds_it(youtube):
    follow_youtuber(youtube, "4343", "pewdiepie")

    a_server_adds("pewdiepie")

    assert renewals_of(youtube, "pewdiepie") == 1
    assert youtube.bot.reminder.created == ["pewdiepie"]
    assert youtube.youtube.subscribe_calls == ["UC-pewdiepie"], "its lease may have lapsed"


async def test_a_followed_youtuber_with_its_renewal_is_already_subscribed(youtube):
    follow_youtuber(youtube, "4343", "pewdiepie")
    a_renewal(youtube, 7001, "pewdiepie")

    a_server_adds("pewdiepie")

    assert renewals_of(youtube, "pewdiepie") == 1
    assert youtube.bot.reminder.created == []
    assert youtube.youtube.subscribe_calls == []


async def test_a_youtuber_nobody_follows_keeps_the_renewal_it_still_has(youtube):
    a_renewal(youtube, 7001, "pewdiepie")

    a_server_adds("pewdiepie")

    assert renewals_of(youtube, "pewdiepie") == 1, "the renewal still running is the one"
    assert youtube.youtube.subscribe_calls == ["UC-pewdiepie"]


async def test_the_start_gives_every_followed_youtuber_without_a_renewal_its_renewal(youtube):
    follow_youtuber(youtube, "4343", "pewdiepie")
    follow_youtuber(youtube, "4444", "mrbeast")
    a_renewal(youtube, 7001, "mrbeast")

    notifications_youtube_video.resubscribe_followed_channels()

    assert renewals_of(youtube, "pewdiepie") == 1
    assert renewals_of(youtube, "mrbeast") == 1
    assert youtube.bot.reminder.created == ["pewdiepie"]


async def test_a_youtuber_asked_for_from_everywhere_at_once_gets_one_renewal(youtube):
    callers = [threading.Thread(target=a_server_adds, args=("pewdiepie",)) for _ in range(3)]
    follow_youtuber(youtube, "4343", "mrbeast")
    callers += [threading.Thread(target=a_server_adds, args=("mrbeast",)) for _ in range(2)]
    callers.append(threading.Thread(
        target=notifications_youtube_video.resubscribe_followed_channels
    ))

    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=10)

    assert renewals_of(youtube, "pewdiepie") == 1
    assert renewals_of(youtube, "mrbeast") == 1
    assert sorted(youtube.bot.reminder.created) == ["mrbeast", "pewdiepie"]


async def test_a_server_adding_a_youtuber_while_its_renewal_ends_gets_one(youtube):
    from app.webhooks.reminder import renew_youtube_subscription

    youtube.mongo_client.guild.moderations.insert_one({"guild_id": "4242", "is_bot_online": False})
    follow_youtuber(youtube, "4242", "pewdiepie")
    a_renewal(youtube, 7001, "pewdiepie")
    reminders = youtube.bot.reminder
    reminders.release.clear()

    ending = threading.Thread(target=renew_youtube_subscription, args=(7001,))
    ending.start()
    assert reminders.deleting.wait(5), "the renewal found nobody following and is deleting itself"
    adding = threading.Thread(target=a_server_adds, args=("pewdiepie",))
    adding.start()
    adding.join(timeout=0.3)
    reminders.release.set()
    ending.join(timeout=5)
    adding.join(timeout=5)
    follow_youtuber(youtube, "4343", "pewdiepie")

    assert reminders.deleted == [7001]
    assert renewals_of(youtube, "pewdiepie") == 1, "the adding server made its own"
    assert reminders.created == ["pewdiepie"]

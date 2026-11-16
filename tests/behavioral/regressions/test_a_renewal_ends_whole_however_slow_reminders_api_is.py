"""A YouTube renewal that ends itself ends whole, however long reminders-api takes.

Reported in the design review: the renewals take turns on an `asyncio.Lock`, held across
the calls to reminders-api, and the reminder webhook reaches the renewal that ends itself
(`drop_unfollowed_renewal`) through `app.data.wait`, which gives up after
`DBConfigs.MONGO_THREAD_WAIT_SECONDS` and cancels the work it waited on. The thread lock it
replaced waited without a limit. So a slow reminders-api cancelled the drop half done: the
reminder was deleted on reminders-api while its schedule row stayed in Mongo, or the drop
never ran because it was still waiting for a subscribe that held the lock.

Shared behaviour: `drop_unfollowed_renewal` (`app/commands/youtube/service.py`), reached
by the reminder webhook through the loop bridge `app.data.wait`, and the lock it shares
with `ensure_renewal`. Exposed by: the review of the move from the thread lock.

Guaranteed: the renewal that ends itself holds the lock only while it counts the followers
and forgets its row, and deletes the reminder after, so a webhook that stops waiting never
leaves a row whose reminder is gone; a callback it was late for answers 500, so
reminders-api may send it again, and that resend either finds nothing left to do or, when
the drop never ran, drops it whole.
"""
import asyncio
import base64
from time import sleep
from types import SimpleNamespace

import pytest

from app.commands.youtube import service as youtube_service
from app.commands.youtube.service import find_reminder_by_id, insert_renewal_reminder
from app.constants import DBConfigs
from app.integrations.http import create_api
from app.integrations.reminders.client import REMINDER_AUTH_USER, ReminderWebhook
from tests.generators.moderations import seed_cog

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]

URL = "/v1/webhooks/reminder"
PASSWORD = "reminders-api-password"
RENEWAL_REMINDER = 7001
SLOW_SECONDS = 0.5
WAIT_SECONDS = 0.1


class SlowReminders(ReminderWebhook):
    """The real credential check, with a reminders-api slower than the webhook waits."""

    def __init__(self):
        super().__init__(SimpleNamespace(config=SimpleNamespace(
            WEBHOOK_URL="https://keiko.test/v1/webhooks",
            REMINDER_APPLICATION_ID="1",
            REMINDER_API_KEY="api-key",
            REMINDER_AUTH_PASSWORD=PASSWORD,
        )))
        self.created = []
        self.deleted = []

    def create_reminder(self, data):
        sleep(SLOW_SECONDS)
        self.created.append(data["notes"])
        return {"id": 8000 + len(self.created)}

    def delete_reminder(self, reminder_id):
        sleep(SLOW_SECONDS)
        self.deleted.append(reminder_id)
        return {}


@pytest.fixture
async def keiko(deps, monkeypatch):
    """The real API app, its waits bridged to this loop and cut short."""
    monkeypatch.setattr(DBConfigs, "MONGO_THREAD_WAIT_SECONDS", WAIT_SECONDS)
    monkeypatch.setattr("app.data._LOOP", asyncio.get_running_loop())
    deps.bot.reminder = SlowReminders()
    await insert_renewal_reminder(RENEWAL_REMINDER, "pewdiepie")
    return SimpleNamespace(
        client=create_api("dev").test_client(),
        reminders=deps.bot.reminder,
        youtube=deps.youtube,
        mongo=deps.mongo_client,
    )


def renewal_fired(keiko):
    token = base64.b64encode(f"{REMINDER_AUTH_USER}:{PASSWORD}".encode()).decode()
    return keiko.client.post(
        URL,
        json={"reminders_notified": [
            {"id": RENEWAL_REMINDER, "title": "youtube_notification", "notes": "pewdiepie"},
        ]},
        headers={"Authorization": f"Basic {token}"},
    )


async def settled(keiko):
    """The renewal's row once the work the webhook stopped waiting for is over."""
    for _ in range(100):
        await asyncio.sleep(0.05)
        if keiko.reminders.deleted:
            break
    await asyncio.sleep(0.1)
    return await find_reminder_by_id(RENEWAL_REMINDER)


async def test_a_renewal_slower_than_the_webhook_waits_still_forgets_its_row(keiko):
    response = await asyncio.to_thread(renewal_fired, keiko)

    assert response.status_code == 500, "the callback it was late for is sent again"
    assert await settled(keiko) is None, "the reminder went, so its row goes too"
    assert keiko.reminders.deleted == [str(RENEWAL_REMINDER)]

    resent = await asyncio.to_thread(renewal_fired, keiko)

    assert resent.status_code == 200
    assert keiko.reminders.deleted == [str(RENEWAL_REMINDER)], "nothing left to delete"
    assert keiko.youtube.subscribe_calls == []


async def test_a_renewal_waiting_behind_a_slow_subscribe_ends_whole_when_sent_again(
    keiko,
):
    seed_cog(keiko.mongo, "notifications_youtube_video", {
        "guild_id": "4343",
        "enabled": True,
        "notifications": {"values": [{"youtuber": {"value": "mrbeast"}}]},
    })
    taken = youtube_service.HubAnswer(
        youtube_service.HubOutcome.DONE, "the hub answered 202", 202
    )
    subscribing = asyncio.ensure_future(youtube_service.ensure_renewal("mrbeast", taken))
    await asyncio.sleep(0.05)

    response = await asyncio.to_thread(renewal_fired, keiko)
    await subscribing

    assert response.status_code == 500
    assert keiko.reminders.deleted == [], "a drop that never ran leaves both in place"
    assert await find_reminder_by_id(RENEWAL_REMINDER) is not None

    resent = await asyncio.to_thread(renewal_fired, keiko)

    assert resent.status_code == 500, "its reminder is still slower than the wait"
    assert await settled(keiko) is None
    assert keiko.reminders.deleted == [str(RENEWAL_REMINDER)]
    assert keiko.reminders.created == ["mrbeast"]

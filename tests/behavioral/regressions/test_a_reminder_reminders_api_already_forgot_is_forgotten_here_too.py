"""A reminder reminders-api no longer has is already deleted, so Keiko forgets it too.

Reported in the v1 review: `audit.reminders` holds renewals whose reminder is gone from
reminders-api. When one of their youtubers has no server left, the renewal deletes
itself: Keiko asks reminders-api to delete the reminder, then deletes its own record.
reminders-api answers 404 for a reminder it does not have, `ReminderWebhook` raised that
404 as a refusal, and the record was never deleted, so the callback failed every time.

Shared behaviour: `ReminderWebhook.delete_reminder`, behind every delete Keiko asks of
reminders-api (the renewal that ends itself, the birthday cleanups, the admin sync
buttons). Exposed by: stale rows in `audit.reminders` in production.

Guaranteed: a 404 to a delete is a reminder already deleted; the local record goes too
and the callback succeeds. Any other refusal still raises with its status and body.
"""
from types import SimpleNamespace

import pytest
import requests

from app.api import create_api
from app.data.reminder import find_reminder_by_id, insert_reminder
from app.integrations.reminder_webhook import (
    REMINDER_API_URL,
    REMINDER_AUTH_USER,
    ReminderAPIError,
    ReminderWebhook,
)
from tests.behavioral.regressions.test_reminder_webhook_trusts_only_the_reminders_api import (
    PASSWORD,
    RENEWAL_REMINDER,
    URL,
    basic,
    renewal,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]


def answer(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = body.encode("utf-8")
    return response


@pytest.fixture
def reminders_api(monkeypatch):
    """reminders-api's DELETE, answering what each test says; every call recorded."""
    api = SimpleNamespace(deleted=[], status=404, body='{"detail": "Not found."}')

    def delete(url, **options):
        api.deleted.append(url)
        return answer(api.status, api.body)

    monkeypatch.setattr(requests, "delete", delete)
    return api


def reminders_client():
    return ReminderWebhook(SimpleNamespace(config=SimpleNamespace(
        WEBHOOK_URL="https://keiko.test/v1/webhooks",
        REMINDER_APPLICATION_ID="1",
        REMINDER_API_KEY="api-key",
        REMINDER_AUTH_PASSWORD=PASSWORD,
    )))


@pytest.fixture
def client(deps):
    deps.bot.reminder = reminders_client()
    return create_api("dev").test_client()


def test_a_renewal_reminders_api_already_deleted_is_forgotten_here_too(
    client, deps, reminders_api
):
    deps.mongo_client.guild.notifications_youtube_video.delete_many({})
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")

    response = client.post(
        URL,
        json={"reminders_notified": [renewal()]},
        headers=basic(REMINDER_AUTH_USER, PASSWORD),
    )

    assert reminders_api.deleted == [f"{REMINDER_API_URL}/reminders/{RENEWAL_REMINDER}"]
    assert response.status_code == 200, "a reminder already deleted is not a failure"
    assert find_reminder_by_id(RENEWAL_REMINDER) is None, "Keiko forgets it too"


def test_any_other_refusal_to_delete_still_raises_with_what_reminders_api_said(
    reminders_api,
):
    reminders_api.status, reminders_api.body = 503, "reminders-api is down"

    with pytest.raises(ReminderAPIError) as refused:
        reminders_client().delete_reminder("7001")

    assert refused.value.status == 503
    assert refused.value.body == "reminders-api is down"

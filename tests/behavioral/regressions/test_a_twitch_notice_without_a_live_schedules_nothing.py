"""/twitch announces only a live that names its streamer, and takes a revocation quietly.

Reported in the design review: the webhook route read the streamer with
`(data.get('event') or {}).get('broadcaster_user_name') or ''`, so a signed EventSub
`revocation` (it carries `subscription.type = "stream.online"` and no `event`), or a
notification whose `event` is missing or null, answered 200 and scheduled
`handle_send_streamer_notification("")`: a Twitch lookup for the user "" and a worker
thread sleeping 15 seconds between attempts. Before the route moved, the same bodies
raised on `data['event']` and answered 500, which scheduled nothing by accident and made
Twitch send the revocation again.

Shared behaviour: the one route every followed streamer's lives arrive on, and the
webhook dispatch that hands it to the Twitch command.
Exposed by: Twitch itself, whenever it revokes a subscription (a streamer gone, the
authorization withdrawn, too many failed deliveries).

Guaranteed: a revocation answers 2xx, so Twitch does not send it again, is logged with the
subscription it ended, and schedules nothing; a notification without a streamer answers
2xx and schedules nothing; a live that names its streamer is still announced.
"""
from types import SimpleNamespace

import pytest
from flask import Flask

import app as app_module
from app import logger as logger_module
from app.integrations.http import webhooks

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]

REVOCATION = {"Twitch-Eventsub-Message-Type": "revocation"}
NOTIFICATION = {"Twitch-Eventsub-Message-Type": "notification"}


@pytest.fixture
def twitch(monkeypatch):
    """The real route and dispatch, with Twitch's signature and the scheduler stubbed."""
    state = SimpleNamespace(scheduled=[], warnings=[])

    def capture(coroutine, name, **kwargs):
        coroutine.close()
        state.scheduled.append(name)

    real_warn = logger_module.warn

    def warn(message, *args, **kwargs):
        state.warnings.append(message)
        real_warn(message, *args, **kwargs)

    monkeypatch.setattr("app.integrations.http.schedule_webhook_job", capture)
    monkeypatch.setattr(logger_module, "warn", warn)
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(
        twitch=SimpleNamespace(
            verify_twitch_signature=lambda request: True,
            check_request_is_a_challenge=lambda request: False,
        ),
        loop=None,
    ), raising=False)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(webhooks)
    state.post = flask_app.test_client().post
    return state


@pytest.mark.parametrize("event_type", ["stream.online", "stream.offline"])
def test_a_revocation_is_taken_and_schedules_nothing(twitch, event_type):
    response = twitch.post("/twitch", headers=REVOCATION, json={
        "subscription": {"type": event_type, "status": "authorization_revoked"},
    })

    assert response.status_code // 100 == 2, "a refused revocation is sent again"
    assert twitch.scheduled == []
    assert any(
        event_type in line and "authorization_revoked" in line
        for line in twitch.warnings
    ), "the log must say which subscription Twitch ended and why"


@pytest.mark.parametrize("body", [
    {"subscription": {"type": "stream.online"}},
    {"subscription": {"type": "stream.online"}, "event": None},
    {"subscription": {"type": "stream.offline"}, "event": {}},
    {"subscription": {"type": "stream.online"}, "event": {"broadcaster_user_name": ""}},
], ids=["no-event", "null-event", "empty-event", "empty-streamer"])
def test_a_notification_without_a_streamer_schedules_nothing(twitch, body):
    response = twitch.post("/twitch", headers=NOTIFICATION, json=body)

    assert response.status_code // 100 == 2
    assert twitch.scheduled == [], "there is no live to announce for streamer ''"


def test_a_live_that_names_its_streamer_is_still_announced(twitch):
    response = twitch.post("/twitch", headers=NOTIFICATION, json={
        "subscription": {"type": "stream.online"},
        "event": {"broadcaster_user_name": "Gaules"},
    })

    assert response.status_code == 200
    assert twitch.scheduled == ["twitch stream.online — gaules"]

"""A webhook request posts to Discord only when it failed, and a refused one never.

Broke as: every request under /v1/webhooks opened a trace that was posted to the
Discord logs channel, the `/healthcheck` an uptime monitor calls every few
seconds included, so the channel filled with messages saying nothing happened.
A refused request was worse: a forged Twitch notice was logged as an error,
which posted to the error channel, and its trace posted to the logs channel, so
a stranger could flood both, bury the real errors and push the bot towards
Discord's limit of invalid requests.

Shared behaviour: `webhook_trace`, `open_webhook_trace` and
`settle_by_status` (`app/webhooks/__init__.py`), `Trace.is_noteworthy`,
`Trace.publishing`, `DiscordLogsHandler.send_trace`, `schedule_webhook_job`.
Consumers: the Twitch, YouTube and reminder webhooks and the jobs they schedule.

Found in the second review: a route that answers a 5xx without raising
logged nothing at error, so its silent trace never posted.

Found on the tree merged with the webhooks branch: YouTube's route answers 503
on purpose while the Data API does not show a new video yet, so the hub
delivers the notice again later; the hook took that for a failure and posted
`answered 503` on every such delivery.

Guaranteed: the healthcheck opens no trace; a request, and the jobs it hands its
work to, post only when they failed, and a warning stays in `guild.logs`; a
route that raises, or answers a 5xx other than 503, still posts its error and
its trace; a 503 without an error asks the sender to try again later, so it
posts nothing and stores no error; a request answered with a 4xx posts nothing,
is counted by route and status, and keeps its lines in `guild.logs`.
"""
import asyncio
import logging
from types import SimpleNamespace

import pytest
from flask import Flask
from prometheus_client import REGISTRY

import app as app_module
import app.webhooks as webhooks_package
from app import logger as logger_module
from app.services import debug_logs
from app.webhooks.jobs import schedule_webhook_job

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def posted(log_channels):
    return (
        log_channels.logs.embeds
        + log_channels.errors.embeds
        + log_channels.calls.embeds
        + log_channels.actions.embeds
    )


def timeline(embed):
    return {field.name: field.value for field in embed.fields}["Timeline"]


@pytest.fixture
def stored_documents():
    """What reached `guild.logs`, read after the request."""
    handler = logger_module.StoredLogsHandler()
    yield debug_logs.drain
    logging.getLogger().removeHandler(handler)


@pytest.fixture
def stored_lines(stored_documents):
    """The messages of what reached `guild.logs`."""
    return lambda: [document["message"] for document in stored_documents()]


@pytest.fixture
def client(monkeypatch):
    """The real blueprint on an app named like production's, so Flask's own error
    log is Keiko's; Twitch signs whatever `signed` says."""
    state = SimpleNamespace(signed=True, scheduled=[])

    def capture(coroutine, name, **kwargs):
        coroutine.close()
        state.scheduled.append(name)

    monkeypatch.setattr("app.webhooks.twitch.schedule_webhook_job", capture)
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(
        twitch=SimpleNamespace(
            verify_twitch_signature=lambda request: state.signed,
            check_request_is_a_challenge=lambda request: False,
        ),
        loop=None,
    ), raising=False)
    flask_app = Flask("app.api")
    flask_app.register_blueprint(webhooks_package.webhooks)
    test_client = flask_app.test_client()
    state.app = flask_app
    state.get = test_client.get
    state.post = test_client.post
    return state


def stream_online(client):
    return client.post("/twitch", json={
        "subscription": {"type": "stream.online"},
        "event": {"broadcaster_user_name": "Gaules"},
    })


def refusals(route, status):
    labels = {"route": route, "status": str(status)}
    return REGISTRY.get_sample_value("keiko_webhook_refusals_total", labels) or 0.0


def test_the_healthcheck_opens_no_trace(client, log_channels):
    response = client.get("/healthcheck")

    assert response.status_code == 200
    assert log_channels.traces == []
    assert posted(log_channels) == []


def test_a_webhook_that_did_its_job_posts_nothing(client, log_channels):
    response = stream_online(client)

    assert response.status_code == 200
    assert len(log_channels.traces) == 1, "the request is still one unit of work"
    assert posted(log_channels) == []


def test_its_lines_still_reach_the_stored_logs(client, log_channels, stored_lines):
    stream_online(client)

    assert any("gaules" in line for line in stored_lines())


def test_a_webhook_that_only_warned_stays_out_of_discord(log_channels, stored_lines):
    with webhooks_package.webhook_trace("reminder"):
        logger_module.warn("Unknown reminder title: something")

    assert posted(log_channels) == []
    assert any("Unknown reminder title" in line for line in stored_lines())


def test_a_webhook_that_failed_still_posts_its_error_and_its_trace(log_channels):
    with webhooks_package.webhook_trace("reminder"):
        logger_module.error("reminders-api refused the payload")

    assert len(log_channels.errors.embeds) == 1, "the error keeps a message of its own"
    [message] = log_channels.logs.embeds
    assert "reminders-api refused the payload" in timeline(message)


def test_a_route_that_raises_still_posts_its_error_and_its_trace(
    client, log_channels, monkeypatch
):
    def broken(request):
        raise RuntimeError("the Twitch secret is not configured")

    monkeypatch.setattr(app_module.bot.twitch, "verify_twitch_signature", broken)

    response = stream_online(client)

    assert response.status_code == 500
    [error] = log_channels.errors.embeds
    assert "the Twitch secret is not configured" in error.description
    [message] = log_channels.logs.embeds
    assert "RuntimeError" in timeline(message)


@pytest.mark.parametrize("status", [500, 502, 504])
def test_a_route_that_answers_a_server_error_without_raising_still_posts(
    client, log_channels, status
):
    client.app.view_functions["webhooks.twitch_webhook"] = lambda: ("Twitch is down", status)

    response = stream_online(client)

    assert response.status_code == status
    [error] = log_channels.errors.embeds
    assert f"/twitch answered {status}" in error.description
    [message] = log_channels.logs.embeds
    assert f"answered {status}" in timeline(message)


def test_a_route_that_asks_to_be_called_again_later_posts_nothing(
    client, log_channels, stored_documents
):
    """A 503 is how a route asks the sender to deliver again later, as YouTube's
    does while the Data API does not show a new video yet."""

    def not_yet():
        logger_module.info("video is not visible yet. The hub will deliver it again.")
        return "Video not visible yet", 503

    client.app.view_functions["webhooks.twitch_webhook"] = not_yet

    response = stream_online(client)

    assert response.status_code == 503
    assert posted(log_channels) == [], "a delivery to try again later is not a failure"
    documents = stored_documents()
    assert [d["message"] for d in documents if d["level"] == "ERROR"] == []
    assert any("not visible yet" in d["message"] for d in documents)


def test_a_forged_request_posts_nothing_and_is_counted(
    client, log_channels, stored_lines
):
    client.signed = False
    before = refusals("/twitch", 403)

    response = stream_online(client)

    assert response.status_code == 403
    assert posted(log_channels) == [], "a refusal is not a failure"
    assert refusals("/twitch", 403) == before + 1
    lines = stored_lines()
    assert any("Invalid Twitch signature" in line for line in lines)
    assert any("refused with 403" in line for line in lines)


def test_a_request_refused_by_flask_itself_is_counted_by_its_status(
    client, log_channels
):
    """The status is read from the response, whoever wrote it: here Flask, for a
    body that is not JSON, while the route reads it."""
    before = refusals("/twitch", 415)

    response = client.post("/twitch", data="not json", content_type="text/plain")

    assert response.status_code == 415
    assert posted(log_channels) == []
    assert refusals("/twitch", 415) == before + 1


async def _settle(log_channels, done, count):
    for _ in range(100):
        if len(done) == count and len(log_channels.traces) == count + 1:
            return
        await asyncio.sleep(0.01)


async def test_the_jobs_of_a_request_stay_quiet_unless_they_fail(
    log_channels, monkeypatch
):
    monkeypatch.setattr(
        app_module, "bot", SimpleNamespace(loop=asyncio.get_running_loop()),
        raising=False,
    )
    done = []

    async def birthday(number):
        if number == 2:
            logger_module.warn(f"birthday reminder {number} found no channel")
        logger_module.info(f"birthday reminder {number} processed")
        done.append(number)

    with webhooks_package.webhook_trace("reminder"):
        schedule_webhook_job(birthday(1), "birthday reminder 1")
        schedule_webhook_job(birthday(2), "birthday reminder 2")
    await _settle(log_channels, done, 2)

    assert done == [1, 2]
    assert posted(log_channels) == [], "two birthdays and a warning are not messages"


async def test_a_job_that_fails_still_posts_its_own_message(log_channels, monkeypatch):
    monkeypatch.setattr(
        app_module, "bot", SimpleNamespace(loop=asyncio.get_running_loop()),
        raising=False,
    )
    done = []

    async def birthday(number):
        if number == 2:
            logger_module.error(f"birthday reminder {number} could not be sent")
        done.append(number)

    with webhooks_package.webhook_trace("reminder"):
        schedule_webhook_job(birthday(1), "birthday reminder 1")
        schedule_webhook_job(birthday(2), "birthday reminder 2")
    await _settle(log_channels, done, 2)

    assert len(log_channels.errors.embeds) == 1
    [message] = log_channels.logs.embeds
    assert "could not be sent" in timeline(message)

"""A webhook that defers its work still has to say what arrived.

Reported: the admin log showed which streamer went offline, but a stream that
went live produced a message with an empty timeline. The offline branch logged
one line synchronously inside the request; the online branch logged nothing and
scheduled a coroutine, and everything that coroutine logged was appended to a
trace the teardown had already closed and posted.

Reported later, from production: that fix posted two messages for one Twitch
live, a `🔔 Webhook Event` naming the streamer and a `⏰ Scheduled Job` with the
fan-out, because the request and the job each closed a trace of their own.

Three things must stay guaranteed:

- every webhook branch names its subject inside the request, so the message
  that arrives is readable even if the deferred work never finishes;
- deferred work never writes into a trace that was already posted;
- the first job a request schedules continues the request's message, so one
  event is one message, and a request that cannot schedule still posts its own.
"""
import asyncio
from types import SimpleNamespace

import pytest
from flask import Flask

import app as app_module
from app import logger as logger_module
from app.services import trace as trace_service
from app.webhooks import webhooks
from app.webhooks.jobs import schedule_webhook_job

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("logging")]


@pytest.fixture
def traces():
    """Every trace that closed, with `logger.*` folded into its timeline."""
    trace_service.clear_sinks()
    captured = []
    trace_service.register_sink(captured.append)

    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)

    yield captured

    logger_module.logger.removeHandler(folding)
    trace_service.clear_sinks()


@pytest.fixture
def twitch_client(monkeypatch):
    """The real blueprint, with the bot and the deferred work stubbed out."""
    scheduled = []

    def capture(coroutine, name, **kwargs):
        coroutine.close()
        scheduled.append(name)

    monkeypatch.setattr("app.webhooks.twitch.schedule_webhook_job", capture, raising=False)
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(
        twitch=SimpleNamespace(
            verify_twitch_signature=lambda request: True,
            check_request_is_a_challenge=lambda request: False,
        ),
        loop=None,
    ), raising=False)

    flask_app = Flask(__name__)
    flask_app.register_blueprint(webhooks)

    return SimpleNamespace(post=flask_app.test_client().post, scheduled=scheduled)


def timeline(trace):
    return " | ".join(line["message"] for line in trace.lines)


def post_event(client, event_type, streamer="Gaules"):
    return client.post("/twitch", json={
        "subscription": {"type": event_type},
        "event": {"broadcaster_user_name": streamer},
    })


def test_a_stream_going_live_names_the_streamer_in_its_log(twitch_client, traces):
    response = post_event(twitch_client, "stream.online")

    assert response.status_code == 200
    assert len(traces) == 1
    assert "gaules" in timeline(traces[0]).lower(), (
        "the message that arrives must say who went live"
    )


def test_a_stream_going_offline_keeps_naming_the_streamer(twitch_client, traces):
    post_event(twitch_client, "stream.offline")

    assert "gaules" in timeline(traces[0]).lower()


def test_both_branches_hand_their_work_to_the_same_scheduler(twitch_client, traces):
    post_event(twitch_client, "stream.online")
    post_event(twitch_client, "stream.offline")

    assert len(twitch_client.scheduled) == 2, (
        "webhooks run on the Flask thread, where create_task is not safe"
    )


async def test_deferred_work_writes_into_its_own_message_not_the_closed_one(traces):
    """The request is posted and gone before the fan-out logs anything."""
    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")

    with trace_service.trace_scope("twitch", source="webhook"):
        logger_module.info("stream.online — gaules")
        deferred = trace_service.run_traced(fan_out(), "twitch stream.online", source="job")

    assert len(traces) == 1, "the request closes when Flask returns"

    await deferred

    assert len(traces) == 2, "the deferred work is its own unit of work"
    assert "Notifications sent" in timeline(traces[1])
    assert "Notifications sent" not in timeline(traces[0]), (
        "a line added to a trace that was already posted is a line nobody reads"
    )


async def test_the_scheduler_puts_the_job_on_the_bot_loop_with_a_trace_of_its_own(
    traces, monkeypatch,
):
    """The one line the original bug lived on, exercised end to end.

    Every other test here stubs the scheduler out, so losing `source="job"`,
    swapping the arguments or going back to `create_task` would pass them all.
    """
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=loop), raising=False)

    ran = asyncio.Event()

    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")
        ran.set()

    schedule_webhook_job(fan_out(), "twitch stream.online — gaules")

    await asyncio.wait_for(ran.wait(), timeout=1)
    for _ in range(100):
        if traces:
            break
        await asyncio.sleep(0.01)

    assert len(traces) == 1
    assert traces[0].source == "job", "a webhook message is not a job message"
    assert traces[0].name == "twitch stream.online — gaules"
    assert "Notifications sent" in timeline(traces[0])


async def test_deferred_work_that_explodes_still_closes_its_message(traces):
    async def broken():
        raise RuntimeError("twitch API is down")

    await trace_service.run_traced(broken(), "twitch stream.online", source="job")

    assert len(traces) == 1
    assert traces[0].has_error


async def _settle(traces, count):
    for _ in range(100):
        if len(traces) >= count:
            return
        await asyncio.sleep(0.01)


def posted(traces):
    return [trace for trace in traces if trace.is_noteworthy]


async def test_a_webhook_that_defers_its_work_posts_one_message_named_after_the_event(
    traces, monkeypatch,
):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=loop), raising=False)
    ran = asyncio.Event()

    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")
        ran.set()

    with trace_service.trace_scope("twitch", source="webhook") as request:
        logger_module.info("stream.online — gaules")
        schedule_webhook_job(fan_out(), "twitch stream.online — gaules")

    await asyncio.wait_for(ran.wait(), timeout=1)
    await _settle(traces, 2)

    messages = posted(traces)
    assert len(messages) == 1, "one Twitch live is one message in the log channel"
    event = messages[0]
    assert event.source == "webhook" and event.name == "twitch"
    assert "gaules" in timeline(event), "the message still says who went live"
    assert "Notifications sent" in timeline(event), "and what was done about it"
    assert event.started_at == request.started_at, (
        "the duration covers the whole event, from arrival to the last guild"
    )


def test_a_hand_over_that_cannot_be_scheduled_leaves_the_request_message_intact(
    traces, monkeypatch,
):
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=None), raising=False)

    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")

    with pytest.raises(Exception):
        with trace_service.trace_scope("twitch", source="webhook"):
            logger_module.info("stream.online — gaules")
            schedule_webhook_job(fan_out(), "twitch stream.online — gaules")

    messages = posted(traces)
    assert len(messages) == 1, "the request is the only message that can arrive"
    assert "gaules" in timeline(messages[0])


async def test_a_second_job_in_the_same_request_gets_a_message_of_its_own(
    traces, monkeypatch,
):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=loop), raising=False)
    done = []

    async def reminder(number):
        logger_module.info(f"birthday reminder {number} processed")
        done.append(number)

    with trace_service.trace_scope("reminder", source="webhook"):
        logger_module.info("2 reminder(s) notified")
        schedule_webhook_job(reminder(1), "birthday reminder 1")
        schedule_webhook_job(reminder(2), "birthday reminder 2")

    for _ in range(100):
        if len(done) == 2:
            break
        await asyncio.sleep(0.01)
    await _settle(traces, 3)

    messages = posted(traces)
    assert len(messages) == 2
    request = next(trace for trace in messages if trace.source == "webhook")
    second = next(trace for trace in messages if trace.source == "job")
    assert "reminder 1 processed" in timeline(request)
    assert "reminder 2 processed" not in timeline(request)
    assert second.name == "birthday reminder 2"

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
from app.services import debug_logs, trace as trace_service
from app.webhooks import webhooks
from app.webhooks.jobs import schedule_webhook_job
import logging
from prometheus_client import REGISTRY
import app.webhooks as webhooks_package

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


async def test_an_error_before_the_hand_over_still_colours_the_message(
    traces, monkeypatch,
):
    """A failure logged in the request survives into the message that reports it.

    What breaks without it: the hand-over copied the request's lines into the
    successor but not the level they were logged at, so a request that had
    already reported an error handed over a red line under a trace that
    believed nothing had gone wrong. The message arrived blue, and the result
    line said the work succeeded while its own timeline showed the failure.
    """
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=loop), raising=False)
    ran = asyncio.Event()

    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")
        ran.set()

    with trace_service.trace_scope("twitch", source="webhook"):
        logger_module.error("Guild 123 refused the notification")
        schedule_webhook_job(fan_out(), "twitch stream.online — gaules")

    await asyncio.wait_for(ran.wait(), timeout=1)
    await _settle(traces, 2)

    event = posted(traces)[0]
    assert "refused" in timeline(event), "the failure is on the message"
    assert event.has_error, "and the message knows it is a failure"
    assert event.result == "failure"


async def settle(traces, count):
    for _ in range(100):
        if len(traces) >= count:
            return
        await asyncio.sleep(0.01)


@pytest.fixture
async def bot_loop(monkeypatch):
    monkeypatch.setattr(
        app_module, "bot", SimpleNamespace(loop=asyncio.get_running_loop()), raising=False
    )


@pytest.mark.regression
async def test_a_line_logged_after_the_hand_over_reaches_the_jobs_message(traces, bot_loop):
    """What a webhook logs after handing its trace to a job reaches the job's message.

    Reported in the wave 1 review: `schedule_webhook_job` hands the request's trace to its
    first job, so one event is one message, and marks the request superseded. Everything the
    request logged afterwards (the next reminder of the same callback, the refusal or the
    failure its status hook writes) was added to the superseded request, which is never
    posted: those lines reached `guild.logs` and no message. And a job that finished before
    the request closed was posted before those lines could arrive.

    Shared behaviour: `Trace.handover` (`app/services/trace.py`), behind every webhook that
    defers its work (Twitch, YouTube, the reminders). Exposed by: a reminder callback whose
    second reminder vanished from the log channel.

    Guaranteed: once a request has handed its trace over, every line it logs lands on the
    job's message, and that message is posted once both the request and the job are done,
    whichever ends first.
    """
    async def birthday():
        logger_module.info("birthday reminder 4001 processed")

    with trace_service.trace_scope("reminder", source="webhook"):
        logger_module.info("2 reminder(s) notified")
        schedule_webhook_job(birthday(), "birthday reminder 4001")
        logger_module.error("reminder 7001 (youtube_notification) failed: Timeout")

    await settle(traces, 2)

    messages = posted(traces)
    assert len(messages) == 1
    assert "reminder 7001" in timeline(messages[0]), "the line after the hand-over arrives"
    assert messages[0].has_error, "and it colours the message it lands on"


@pytest.mark.regression
async def test_the_jobs_message_waits_for_the_request_that_handed_it_over(traces, bot_loop):
    async def quick():
        logger_module.info("Notifications sent for gaules in 0 guilds")

    with trace_service.trace_scope("twitch", source="webhook"):
        logger_module.info("stream.online — gaules")
        schedule_webhook_job(quick(), "twitch stream.online — gaules")
        await asyncio.sleep(0.05)
        logger_module.warn("/v1/webhooks/twitch answered slowly")

    await settle(traces, 2)

    messages = [trace for trace in traces if not trace.superseded]
    assert len(messages) == 1, "one event, one message"
    assert "answered slowly" in timeline(messages[0]), (
        "the job ended first, and its message still waited for the request's last line"
    )


def embeds_posted(log_channels):
    return (
        log_channels.logs.embeds
        + log_channels.errors.embeds
        + log_channels.calls.embeds
        + log_channels.actions.embeds
    )


def embed_timeline(embed):
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


@pytest.mark.regression
def test_the_healthcheck_opens_no_trace(client, log_channels):
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
    response = client.get("/healthcheck")

    assert response.status_code == 200
    assert log_channels.traces == []
    assert embeds_posted(log_channels) == []


@pytest.mark.regression
def test_a_webhook_that_did_its_job_posts_nothing(client, log_channels):
    response = stream_online(client)

    assert response.status_code == 200
    assert len(log_channels.traces) == 1, "the request is still one unit of work"
    assert embeds_posted(log_channels) == []


@pytest.mark.regression
def test_its_lines_still_reach_the_stored_logs(client, log_channels, stored_lines):
    stream_online(client)

    assert any("gaules" in line for line in stored_lines())


@pytest.mark.regression
def test_a_webhook_that_only_warned_stays_out_of_discord(log_channels, stored_lines):
    with webhooks_package.webhook_trace("reminder"):
        logger_module.warn("Unknown reminder title: something")

    assert embeds_posted(log_channels) == []
    assert any("Unknown reminder title" in line for line in stored_lines())


@pytest.mark.regression
def test_a_webhook_that_failed_still_posts_its_error_and_its_trace(log_channels):
    with webhooks_package.webhook_trace("reminder"):
        logger_module.error("reminders-api refused the payload")

    assert len(log_channels.errors.embeds) == 1, "the error keeps a message of its own"
    [message] = log_channels.logs.embeds
    assert "reminders-api refused the payload" in embed_timeline(message)


@pytest.mark.regression
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
    assert "RuntimeError" in embed_timeline(message)


@pytest.mark.regression
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
    assert f"answered {status}" in embed_timeline(message)


@pytest.mark.regression
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
    assert embeds_posted(log_channels) == [], "a delivery to try again later is not a failure"
    documents = stored_documents()
    assert [d["message"] for d in documents if d["level"] == "ERROR"] == []
    assert any("not visible yet" in d["message"] for d in documents)


@pytest.mark.regression
def test_a_forged_request_posts_nothing_and_is_counted(
    client, log_channels, stored_lines
):
    client.signed = False
    before = refusals("/twitch", 403)

    response = stream_online(client)

    assert response.status_code == 403
    assert embeds_posted(log_channels) == [], "a refusal is not a failure"
    assert refusals("/twitch", 403) == before + 1
    lines = stored_lines()
    assert any("Invalid Twitch signature" in line for line in lines)
    assert any("refused with 403" in line for line in lines)


@pytest.mark.regression
def test_a_request_refused_by_flask_itself_is_counted_by_its_status(
    client, log_channels
):
    """The status is read from the response, whoever wrote it: here Flask, for a
    body that is not JSON, while the route reads it."""
    before = refusals("/twitch", 415)

    response = client.post("/twitch", data="not json", content_type="text/plain")

    assert response.status_code == 415
    assert embeds_posted(log_channels) == []
    assert refusals("/twitch", 415) == before + 1


async def _settle_channels(log_channels, done, count):
    for _ in range(100):
        if len(done) == count and len(log_channels.traces) == count + 1:
            return
        await asyncio.sleep(0.01)


@pytest.mark.regression
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
    await _settle_channels(log_channels, done, 2)

    assert done == [1, 2]
    assert embeds_posted(log_channels) == [], "two birthdays and a warning are not messages"


@pytest.mark.regression
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
    await _settle_channels(log_channels, done, 2)

    assert len(log_channels.errors.embeds) == 1
    [message] = log_channels.logs.embeds
    assert "could not be sent" in embed_timeline(message)

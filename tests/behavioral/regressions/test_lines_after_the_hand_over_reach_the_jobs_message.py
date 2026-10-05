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
import asyncio
from types import SimpleNamespace

import pytest

import app as app_module
from app import logger as logger_module
from app.services import trace as trace_service
from app.webhooks.jobs import schedule_webhook_job
from tests.behavioral.regressions.test_webhook_background_trace import (  # noqa: F401
    posted,
    timeline,
    traces,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.regression, pytest.mark.shared_contract("logging")]


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


async def test_a_line_logged_after_the_hand_over_reaches_the_jobs_message(traces, bot_loop):
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

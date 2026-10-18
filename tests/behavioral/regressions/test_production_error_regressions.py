"""Failures the production archive recorded, pinned so they cannot return.

Two years of daily log files were indexed and grouped by signature. These are
the ones whose cause still lived in the code. Each test names the count the
archive holds, because a regression here is not hypothetical: it already
happened that many times.
"""
import logging

import pytest

from app import logger as logger_module
from app.constants import DiscordLimits as limits
from app.constants import LogTypes as logconstants
from app.services import block_links
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import requests
from tests.mocks.discord import MockInteraction, create_member

pytestmark = [pytest.mark.regression, pytest.mark.unit]


# --------------------------------------------------------------------------
# "In embeds.0.description: Must be 4096 or fewer in length." — 141 occurrences
# --------------------------------------------------------------------------

def make_record(message, level=logging.ERROR, **extra):
    record = logging.LogRecord(
        name="keiko", level=level, pathname="app/services/thing.py",
        lineno=1, msg=message, args=(), exc_info=None,
    )
    record.asctime = "2026-08-23 10:00:00"
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.fixture
def handler():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    bot = SimpleNamespace(loop=MagicMock(), config=SimpleNamespace(
        ADMIN_LOGS_CHANNEL_ID=1, ADMIN_LOGS_ERROR_CHANNEL_ID=2,
        ADMIN_LOGS_COMMAND_CALL_ID=3, ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
    ), get_channel=lambda _id: None)

    instance = logger_module.DiscordLogsHandler(bot)
    logger_module.logger.removeHandler(instance)
    return instance


def test_a_huge_log_message_does_not_blow_past_the_embed_limit(handler):
    """Discord rejects the whole message, so the log about the failure is the
    thing that fails to be logged."""
    embed = handler.add_embed(make_record(
        "x" * 9000, log_type=logconstants.COMMAND_ERROR_TYPE
    ))

    assert len(embed.description) <= limits.EMBED_DESCRIPTION


def test_an_enormous_traceback_does_not_blow_past_the_embed_limit(handler):
    """`format_traceback_message` caps the frames at 3000 characters, but the
    exception text and the file path are appended afterwards and were not
    capped at all."""
    try:
        raise ValueError("y" * 6000)
    except ValueError:
        import sys
        record = make_record("boom")
        record.exc_info = sys.exc_info()
        embed = handler.add_embed(record)

    assert len(embed.description) <= limits.EMBED_DESCRIPTION


def test_the_clipped_description_keeps_the_beginning(handler):
    embed = handler.add_embed(make_record(
        "the part that matters " + "x" * 9000,
        log_type=logconstants.COMMAND_ERROR_TYPE,
    ))

    assert embed.description.startswith("the part that matters")


# --------------------------------------------------------------------------
# "KeyError: 'allowed_chats'" — 210 occurrences, on every message
# --------------------------------------------------------------------------

def test_a_config_saved_before_the_field_existed_does_not_raise():
    """Broke as: `config[ALLOWED_CHATS_KEY]["values"]` on a document written
    before that field existed, inside on_message, so it fired on every message
    that guild received.

    The block-links redesign fixed it by normalizing first. This pins the
    guarantee rather than the current call order, because the subscript is
    still there and only the normalizer keeps it safe.
    """
    legacy = {"block_links": {"enabled": True}}

    normalized = block_links.normalize_block_links_config(legacy)

    assert "values" in normalized["allowed_chats"]
    assert normalized["allowed_chats"]["values"] == []


def test_evaluating_a_message_against_a_legacy_config_does_not_raise():
    subject = block_links.MessageSubject(
        content="look at https://example.com",
        channel_id="100",
        author_role_ids=(),
    )

    evaluation = block_links.evaluate_message(subject, {"block_links": {"enabled": True}})

    assert evaluation is not None


def refused():
    response = requests.Response()
    response.status_code = 500
    response._content = b'{"object": "error", "message": "Notion is down"}'
    return response


def a_gateway_page():
    response = requests.Response()
    response.status_code = 502
    response._content = b"<html><head><title>502 Bad Gateway</title></head></html>"
    return response


def timed_out():
    raise requests.ReadTimeout("api.notion.com did not answer in 15 seconds")


@pytest.fixture
def report(deps, guild, monkeypatch):
    from app.cogs.base.report import Report

    reports = MagicMock()
    reports.send = AsyncMock()
    notion = SimpleNamespace(create_report=None)
    bot = SimpleNamespace(
        notion=notion,
        config=SimpleNamespace(ADMIN_REPORTS_CHANNEL_ID=1),
        get_channel=lambda channel_id: reports,
    )
    direct_messages = AsyncMock()
    monkeypatch.setattr(Report, "_send_report_dm", direct_messages)
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.command = SimpleNamespace(qualified_name="report")

    async def send(answer):
        notion.create_report = lambda **fields: answer()
        await Report.report.callback(
            Report(bot), interaction, "title", "description", "block_links"
        )

    return SimpleNamespace(
        send=send, reports=reports, interaction=interaction, direct_messages=direct_messages
    )


@pytest.mark.behavioral
@pytest.mark.parametrize(
    "answer",
    [timed_out, refused, a_gateway_page],
    ids=["timeout", "error-answer", "gateway-page"],
)
async def test_a_report_notion_does_not_take_still_arrives(report, answer, caplog):
    """A `/report` Notion does not take still reaches the team and the person who sent it.

    Reported in the v1 review, once every outside call got a time limit: Notion that does
    not answer in time now raises where it used to hang the bot, and `/report` raised with
    it, so the report was lost and the person saw an error. A Notion error answer was
    already lost the same way: the ticket number was read from a body that has none.

    Shared behaviour: `Report.report`, the one way a server reports a bug. Exposed by: the
    timeouts of the shared HTTP client.

    Guaranteed: when Notion fails or refuses, the report still goes to the reports channel
    under a Keiko ticket number, the person is answered and gets the copy in their DMs, and
    the failure is an error in the error channel: an exception named by its type only, a
    refusal by its status code and the start of its body, which may not be JSON at all (the
    gateway page of a 502 or 504 once made the error log itself raise and lose the report).
    """
    with caplog.at_level("INFO"):
        await report.send(answer)

    posted = report.reports.send.await_args.kwargs["embed"]
    assert "KEIKO-" in posted.title, "the report carries a ticket number of Keiko's"
    assert report.interaction.get_last_response()["type"] == "followup_send"
    report.direct_messages.assert_awaited_once()
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert errors and all("Notion" in record.getMessage() for record in errors)


@pytest.mark.behavioral
async def test_a_refusal_is_logged_by_its_status_and_the_start_of_its_body(report, caplog):
    with caplog.at_level("INFO"):
        await report.send(a_gateway_page)

    [error] = [record for record in caplog.records if record.levelname == "ERROR"]
    assert "502" in error.getMessage()
    assert "<html><head><title>502 Bad Gateway" in error.getMessage()

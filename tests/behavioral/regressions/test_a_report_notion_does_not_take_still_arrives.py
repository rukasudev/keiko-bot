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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests

from tests.mocks.discord import MockInteraction, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


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


@pytest.mark.parametrize(
    "answer",
    [timed_out, refused, a_gateway_page],
    ids=["timeout", "error-answer", "gateway-page"],
)
async def test_a_report_notion_does_not_take_still_arrives(report, answer, caplog):
    with caplog.at_level("INFO"):
        await report.send(answer)

    posted = report.reports.send.await_args.kwargs["embed"]
    assert "KEIKO-" in posted.title, "the report carries a ticket number of Keiko's"
    assert report.interaction.get_last_response()["type"] == "followup_send"
    report.direct_messages.assert_awaited_once()
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert errors and all("Notion" in record.getMessage() for record in errors)


async def test_a_refusal_is_logged_by_its_status_and_the_start_of_its_body(report, caplog):
    with caplog.at_level("INFO"):
        await report.send(a_gateway_page)

    [error] = [record for record in caplog.records if record.levelname == "ERROR"]
    assert "502" in error.getMessage()
    assert "<html><head><title>502 Bad Gateway" in error.getMessage()

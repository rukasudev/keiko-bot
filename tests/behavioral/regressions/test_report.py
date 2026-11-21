"""/reportar (`report`): a report reaches the team and its author whatever the author's
DMs allow, the file it carries outlives Discord's link to it, a member never sends more
than the day's limit however the modals race, and whatever breaks, its author or the
operator who clicked is told."""
import asyncio
import time

import pytest
import requests

from app.constants import Commands, ViewConstants
from app.core.utils import ml
from tests.behavioral.flows.report import (
    PAGE_ID,
    click_status,
    errors_logged,
    give_status,
    no_status_property,
    open_modal,
    page_created,
    reporting,
    send_report,
    written,
)
from tests.behavioral.harness.copy_keys import copy_text
from tests.mocks.web import respond

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def private_answer(scenario):
    """The last answer only the author sees."""
    event = next(
        event for event in reversed(scenario.outputs)
        if event["kind"] in ("send", "followup_send") and event.get("ephemeral")
    )
    return event["embed"]


def broken():
    raise RuntimeError("something nobody expected")


def timed_out():
    raise requests.ReadTimeout("api.notion.com did not answer in 15 seconds")


def a_gateway_page():
    body = b"<html><head><title>502 Bad Gateway</title></head></html>"
    return respond(
        "https://api.notion.com/v1/pages", body, status=502, content_type="text/html"
    )


def slowed_once(client, monkeypatch, seconds):
    """Redis answers its next pipeline `seconds` late, then on time again."""
    pipeline = client.pipeline
    slowed = []

    def slow_pipeline(*args, **kwargs):
        queued = pipeline(*args, **kwargs)
        if not slowed:
            slowed.append(queued)
            execute = queued.execute

            def late():
                time.sleep(seconds)
                return execute()

            queued.execute = late
        return queued

    monkeypatch.setattr(client, "pipeline", slow_pipeline)


async def test_a_reporter_whose_dms_are_closed_still_gets_the_ticket_number(
    scenario_factory,
):
    """An author who takes no DMs is told the report went through, with its number.

    Broke as: `/report` sent its copy of the ticket by DM with no answer for a closed DM
    (`_send_report_dm` in `app/commands/report/cog.py`), so Discord's 403 went up to the
    tree's error handler after the report had already reached the team and Notion: it
    was logged as a failure, recorded as `command.failed`, and the author's answer turned
    into the generic error, with no ticket number to quote. Shared behaviour: none, the
    report's own DM. Consumer that exposed it: a member whose server privacy settings
    close DMs from server members. Guaranteed: with the DM refused, the report still
    reaches the reports channel and Notion, nothing raises, and the author's private
    answer names the ticket number and carries the link to Keiko's official server.
    """
    report = reporting(scenario_factory)
    report.reporter.dms_open = False

    await send_report(report)

    [ticket] = report.channel.tickets
    assert "#KEIKO-1234" in ticket.embeds[0].title
    assert len(report.notion.created) == 1
    assert "#KEIKO-1234" in private_answer(report.scenario)["description"]
    support = support_buttons(report.scenario)
    assert support, "the answer carries the link to the official server"


async def test_a_reported_file_stays_on_the_ticket_after_discords_link_expires(
    scenario_factory,
):
    """The file a member reports with is kept on the ticket, and Notion links the ticket.

    Broke as: the ticket's Attachment field and Notion's "Anexo" kept the upload's own
    link (`attachment.url`, `app/commands/report/cog.py`), a signed CDN link Discord lets
    live `DiscordLimits.ATTACHMENT_URL_SECONDS` (a day), so the team opened a dead link
    on any report older than that. Shared behaviour: none, the report's ticket. Consumer
    that exposed it: the v1 review of /report. Guaranteed: the file itself is posted on
    the ticket in the reports channel, and Notion's "Anexo" is that message's link,
    never the upload's CDN link.
    """
    report = reporting(scenario_factory)

    await send_report(report, file="print.png")

    [ticket] = report.channel.tickets
    [posted] = report.channel.files
    assert posted is not None and posted.filename == "print.png"
    [page] = report.notion.created
    assert page["attachment_url"] == ticket.jump_url


async def test_a_report_that_breaks_unexpectedly_still_answers_its_author(
    scenario_factory, caplog
):
    """A report that fails in a way nobody planned for ends in the generic error.

    Broke as: the report's modal had no `on_error`, so an exception after its
    `defer(thinking=True)` left the member on Keiko's "thinking" for good and reached
    only discord.py's own log, where the old slash command went through the tree's error
    handler (the error channel and `errors.generic`). Shared behaviour: `ReportModal`.
    Consumer that exposed it: the review of PR9. Guaranteed: the member is answered
    privately with the generic error, and the failure is an error log with its traceback
    and the interaction.
    """
    report = reporting(scenario_factory)
    report.channel.refusal = RuntimeError("the channel broke")

    with caplog.at_level("ERROR"):
        await send_report(report)

    answer = report.scenario.outputs[-1]
    assert answer["kind"] == "followup_send" and answer["ephemeral"]
    assert answer["embed"]["title"].endswith(copy_text("errors.generic.title", "pt-br"))
    [error] = errors_logged(caplog)
    assert error.exc_info is not None and error.interaction is not None


@pytest.mark.parametrize("status", ["resolved", "details"])
async def test_a_status_click_that_breaks_unexpectedly_still_answers_the_operator(
    scenario_factory, caplog, status
):
    """A status click that fails in a way nobody planned for ends in the generic error.

    Broke as: discord.py hands a dynamic item's exception to its own log only, and the
    ticket's status button caught none, so the operator who clicked saw nothing at all
    and the error channel never heard of it. Shared behaviour: `ReportStatusButton`.
    Consumer that exposed it: the review of PR9. Guaranteed: the operator is answered
    privately with the generic error, the ticket keeps its status, and the failure is an
    error log with its traceback and the interaction, on a status set at once and on the
    need details the operator's question sets.
    """
    report = reporting(scenario_factory)
    await send_report(report)
    [ticket] = report.channel.tickets
    report.notion.status_answer = broken

    with caplog.at_level("ERROR"):
        await give_status(report, ticket, status)

    answer = report.scenario.outputs[-1]
    assert answer["kind"] == "followup_send" and answer["ephemeral"]
    assert answer["embed"]["title"].endswith(copy_text("errors.generic.title", "en-us"))
    assert all(field.name != "Status" for field in ticket.embeds[0].fields)
    [error] = errors_logged(caplog)
    assert error.exc_info is not None and error.interaction is not None


async def test_a_slow_redis_opens_the_report_and_counts_it_when_sent(
    scenario_factory, deps, monkeypatch
):
    """Redis slower than Discord's three seconds never keeps the modal from opening.

    Broke as: /reportar read the member's count for the day before `send_modal`, which
    Discord takes only within three seconds of the click, while the Redis client waits
    `DBConfigs.REDIS_SOCKET_TIMEOUT_SECONDS` (five), so a slow Redis made the slash
    command fail where a Redis that refused at once let the report through. Shared
    behaviour: the open of `/report`. Consumer that exposed it: the review of PR9.
    Guaranteed: the open waits for the count at most
    `ViewConstants.READ_BEFORE_ANSWER_SECONDS`, the modal opens when Redis is slower,
    and the report is still counted when sent, so past the day's limit it is refused
    then and reaches nobody.
    """
    report = reporting(scenario_factory)
    for _ in range(Commands.REPORT_DAILY_LIMIT):
        await send_report(report)
    monkeypatch.setattr(ViewConstants, "READ_BEFORE_ANSWER_SECONDS", 0.01)
    slowed_once(deps.redis_client, monkeypatch, 0.2)

    await open_modal(report)

    assert report.scenario.pending_modal is not None
    await report.scenario.submit_modal(written(report))
    assert len(report.channel.tickets) == Commands.REPORT_DAILY_LIMIT
    answer = private_answer(report.scenario)
    assert answer["title"] == copy_text("report.ui.responses.limit.title", "pt-br")


async def test_two_reports_sent_at_once_at_the_last_one_post_only_one(
    scenario_factory, deps
):
    """Two modals sent in the same instant at the day's last report post one of them.

    Broke as: the send read the day's count, posted the ticket and only then counted it,
    so two sends that both read four reached the team and the member had six reports
    that day. Shared behaviour: the daily count of `submit_report`. Consumer that
    exposed it: the review of PR9. Guaranteed: a send counts itself before anything is
    posted, the one past the limit is refused and gives its count back, and the day's
    count is the reports that reached the team.
    """
    report = reporting(scenario_factory)
    for _ in range(Commands.REPORT_DAILY_LIMIT - 1):
        await send_report(report)
    await open_modal(report)
    on_the_phone = report.scenario.pending_modal
    await open_modal(report)

    await asyncio.gather(
        report.scenario.submit_modal(written(report)),
        report.scenario.submit_modal(written(report), modal=on_the_phone),
    )

    posted = Commands.REPORT_DAILY_LIMIT
    assert len(report.channel.tickets) == len(report.notion.created) == posted
    key = Commands.REDIS_REPORT_WINDOW.format(user_id=report.reporter.id)
    assert int(deps.redis_client.get(key)) == posted


async def test_a_ticket_numbered_in_another_shape_still_takes_a_status(
    scenario_factory,
):
    """The status buttons route whatever ticket id Notion's `ID` property writes.

    Broke as: the buttons' custom id template read the ticket as `[A-Z]+-\\d+`, so a
    database whose `ID` prefix holds a lowercase letter or a digit drew buttons no click
    ever reached. Shared behaviour: `ReportStatusButton`. Consumer that exposed it: the
    review of PR9. Guaranteed: a ticket of letters, digits and dashes takes its status.
    """
    report = reporting(scenario_factory)
    report.notion.answer = lambda: page_created(prefix="Bug2")
    await send_report(report)
    [ticket] = report.channel.tickets

    await click_status(report, ticket, "resolved")

    assert report.notion.statuses == [
        (PAGE_ID.replace("-", ""), Commands.REPORT_STATUSES["resolved"])
    ]


@pytest.mark.parametrize(
    "answer, wants_the_property",
    [
        (no_status_property, True),
        (a_gateway_page, False),
        (timed_out, False),
        (lambda: None, False),
    ],
    ids=["no-status-property", "gateway-page", "timeout", "integration-off"],
)
async def test_a_status_notion_refuses_names_the_fix_only_when_it_is_the_property(
    scenario_factory, answer, wants_the_property
):
    """The operator reads what Notion said once, and the Status hint only on a 400.

    Broke as: the refusal always closed on "The reports database needs a **Status**
    property", for a timeout and a turned off integration too, and put its own period
    after Notion's message, which already ends in one ("`Status is not a property that
    exists.`."). Shared behaviour: the operator's refusal of `set_status`. Consumer that
    exposed it: the owner's print of the status refusal. Guaranteed: the property and
    its options are named only when Notion answers 400, and no sentence ends twice.
    """
    report = reporting(scenario_factory)
    report.notion.status_answer = answer
    await send_report(report)
    [ticket] = report.channel.tickets

    await click_status(report, ticket, "review")

    told = report.scenario.outputs[-1]["embed"]["description"]
    names = Commands.REPORT_STATUSES.values()
    assert all(name in told for name in names) is wants_the_property
    assert any(name in told for name in names) is wants_the_property
    assert ".`." not in told and ".." not in told


def support_buttons(scenario):
    """The link buttons to Keiko's official server on the last private answer."""
    answer = next(
        event
        for event in reversed(scenario.outputs)
        if event["kind"] in ("send", "followup_send") and event.get("ephemeral")
    )
    label = ml("buttons.support.label", scenario.locale_str)
    return [
        child
        for row in answer.get("components") or []
        for child in row.get("children", [row])
        if child.get("label") == label and child.get("style") == "link"
    ]

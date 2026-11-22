"""Behavioral scenarios: /reportar, from the member's modal to the team's ticket and
back to the member when the team gives the report a status.

Guaranteed: /reportar opens a modal (what happened, the command, a file); the report
reaches the reports channel with its file and Notion with its first words as the
title and the English name of the command; its author is answered privately and gets
the ticket by DM; a member sends at most `Commands.REPORT_DAILY_LIMIT` reports in a
day, a report that reaches nobody is not counted, and a Redis outage never closes the
door;
a ticket whose Notion page exists carries three status buttons, and a click sets the
status on Notion, tells the author by DM in the report's language, writes it on the
ticket and paints the ticket in the status's colour (in review Keiko's blue, resolved
green, need details the warning yellow), while a Notion refusal changes nothing and is
told to the operator;
need details first asks the operator for a question, which reaches the author quoted in
the DM with a blue Answer button, stays on the ticket and is posted in the ticket's
thread, opened on the first question and reused after; only the author may answer, as
often as they like, and each answer lands in that thread, found again after a restart,
while the author is thanked privately; a thread Keiko may not open or post in sends the
answer under the ticket with one warning, and an answer that reached nobody is told to
the author privately.
"""

from datetime import timedelta
from types import SimpleNamespace

import discord
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.constants import Commands, Style
from app.core.utils import format_discord_timestamp, ml
from tests.behavioral.flows.report import (
    ANSWER,
    OTHER,
    PAGE_ID,
    PAGE_URL,
    QUESTION,
    TEXT,
    Tree,
    answer_button,
    answer_question,
    answer_text,
    ask_details,
    click_answer,
    click_status,
    errors_logged,
    give_status,
    modal_text,
    no_status_property,
    open_modal,
    page_created,
    refused,
    reporting,
    send_report,
    slash,
    thread_of,
    written,
)
from tests.behavioral.harness.copy_keys import copy_text
from tests.behavioral.harness.message_store import FIXED_MESSAGE_TIME
from tests.mocks.discord import create_member

pytestmark = pytest.mark.behavioral

LONG = (
    "Eu abri o /configurar, escolhi as notificações da Twitch, cliquei em Salvar e a "
    "mensagem sumiu sem dizer nada, e o canal continuou sem nenhum aviso"
)
STATUS_TEXTS = {
    "review": "report.ui.responses.status.description",
    "resolved": "report.ui.responses.status.overrides.0.description",
    "details": "report.ui.responses.status.overrides.1.description",
}
STATUS_COLOURS = {
    "review": int(Style.BACKGROUND_COLOR, 16),
    "resolved": discord.Color.green().value,
    "details": int(Style.WARNING_COLOR, 16),
}


def private_answer(scenario):
    """The last answer only the member sees."""
    return next(
        event["embed"]
        for event in reversed(scenario.outputs)
        if event["kind"] in ("send", "followup_send") and event.get("ephemeral")
    )


def ticket_fields(ticket):
    return {field.name: field.value for field in ticket.embeds[0].fields}


async def asked(scenario_factory, locale="pt-br", question=QUESTION):
    """A report whose ticket the team gave need details, asking `question`."""
    report = reporting(scenario_factory, locale)
    await send_report(report)
    [ticket] = report.channel.tickets
    await ask_details(report, ticket, question)
    return report, ticket


async def test_reportar_opens_a_modal_with_what_happened_the_command_and_a_file(
    scenario_factory, each_locale
):
    report = reporting(scenario_factory, each_locale)
    report.bot.tree = Tree(slash("ping"))

    await open_modal(report)

    assert report.scenario.pending_modal.title == modal_text("title", each_locale)
    assert report.scenario.pending_modal_fields() == [
        modal_text(f"{name}_label", each_locale) for name in ("text", "command", "file")
    ]
    text, command, file = report.scenario.pending_modal_inputs()
    assert (text.style, text.required, text.max_length) == (
        discord.TextStyle.long,
        True,
        Commands.REPORT_TEXT_MAX,
    )
    offered = [(option.label, option.value) for option in command.options]
    assert command.required and offered == [
        ("/ping", "/ping"),
        (modal_text("other_label", each_locale), OTHER),
    ]
    assert file.required is False


async def test_a_report_reaches_notion_with_its_first_words_and_the_english_command(
    scenario_factory, analytics_events
):
    report = reporting(scenario_factory)
    report.bot.tree = Tree(slash("ping"))

    await send_report(report, LONG, command="/ping")

    [page] = report.notion.created
    assert (
        page["title"].endswith("…") and len(page["title"]) <= Commands.REPORT_TITLE_MAX
    )
    assert LONG.startswith(page["title"][:-1])
    assert (page["description"], page["command"], page["author"]) == (
        LONG,
        "/ping",
        "555",
    )
    assert page["attachment_url"] is None
    [submitted] = [
        event for event in analytics_events if event["type"] == "report.submitted"
    ]
    assert submitted["props"]["command"] == "/ping"


async def test_the_author_is_answered_privately_and_gets_the_ticket_by_dm(
    scenario_factory, each_locale
):
    report = reporting(scenario_factory, each_locale)

    await send_report(report, file="print.png")

    answer = private_answer(report.scenario)
    assert answer["title"] == copy_text("report.ui.responses.sent.title", each_locale)
    assert answer["description"].startswith(
        copy_text("report.ui.responses.sent.description", each_locale)
    )
    [dm] = report.reporter.dms
    assert dm.title == copy_text(
        "report.ui.responses.ticket.title", each_locale, ticket="KEIKO-1234"
    )
    fields = {field.name: field.value for field in dm.fields}
    assert fields[
        copy_text("report.ui.responses.ticket.fields.command.title", each_locale)
    ] == (modal_text("other_label", each_locale))
    assert (
        fields[
            copy_text("report.ui.responses.ticket.fields.attachment.title", each_locale)
        ]
        == "print.png"
    )
    assert (
        fields[
            copy_text(
                "report.ui.responses.ticket.fields.description.title", each_locale
            )
        ]
        == TEXT
    )


async def test_the_ticket_names_its_file_its_notion_page_and_carries_the_status_buttons(
    scenario_factory,
):
    report = reporting(scenario_factory)

    await send_report(report, file="print.png")

    [ticket] = report.channel.tickets
    assert ticket.embeds[0].title == "📩 New ticket created [#KEIKO-1234]!"
    fields = ticket_fields(ticket)
    assert (fields["Attachment"], fields["Notion Ticket Url"]) == (
        "print.png",
        PAGE_URL,
    )
    buttons = [
        item
        for item in ticket.registered_items
        if isinstance(item, discord.ui.DynamicItem)
    ]
    assert [button.item.label for button in buttons] == [
        "In review",
        "Resolved",
        "Need details",
    ]


async def test_a_file_over_the_upload_cap_is_linked_and_marked_as_expiring(
    scenario_factory,
):
    report = reporting(scenario_factory)
    report.channel.guild.filesize_limit = 1

    await send_report(report, file="video.mp4")

    assert report.channel.files == [None]
    attachment = ticket_fields(report.channel.tickets[0])["Attachment"]
    assert (
        attachment.startswith("https://cdn.discordapp.com/") and "a day" in attachment
    )


async def test_a_ticket_notion_did_not_create_has_no_status_buttons(scenario_factory):
    report = reporting(scenario_factory)
    report.notion.answer = lambda: None

    await send_report(report)

    [ticket] = report.channel.tickets
    assert ticket.view is None
    assert "Notion Ticket Url" not in ticket_fields(ticket)


@pytest.mark.parametrize(
    ("prefix", "buttons"),
    [
        pytest.param("Bug_2 x", 3, id="an-unusual-prefix"),
        pytest.param("X" * 90, 0, id="a-prefix-too-long-for-a-button"),
    ],
)
async def test_a_ticket_keeps_its_number_whatever_shape_notion_gives_it(
    scenario_factory, prefix, buttons
):
    """Broke as: Notion's ticket number was written into the status buttons' custom id,
    and a number outside the id's pattern or too long for it raised after the ticket was
    already posted, so the author got an error for a report the team had received.
    Shared behaviour: the status buttons' custom id. Consumer: /reportar on a database
    whose ID prefix the owner picks. Guaranteed: the ticket always carries its number;
    the buttons come when the number fits in them."""
    report = reporting(scenario_factory)
    report.notion.answer = lambda: page_created(prefix=prefix)

    await send_report(report)

    [ticket] = report.channel.tickets
    assert f"#{prefix}-1234" in ticket.embeds[0].title
    drawn = [
        item
        for item in ticket.registered_items
        if isinstance(item, discord.ui.DynamicItem)
    ]
    assert len(drawn) == buttons
    assert copy_text("report.ui.responses.sent.title", "pt-br") in str(
        private_answer(report.scenario)
    )


async def test_the_sixth_report_in_a_day_is_refused_and_reaches_nobody(
    scenario_factory, each_locale
):
    report = reporting(scenario_factory, each_locale)
    for _ in range(Commands.REPORT_DAILY_LIMIT):
        await send_report(report)

    await open_modal(report)

    assert report.scenario.pending_modal is None
    frees = FIXED_MESSAGE_TIME + timedelta(seconds=Commands.REPORT_DAILY_WINDOW_SECONDS)
    answer = private_answer(report.scenario)
    assert answer["title"] == copy_text("report.ui.responses.limit.title", each_locale)
    assert answer["description"].startswith(
        copy_text(
            "report.ui.responses.limit.description",
            each_locale,
            count=Commands.REPORT_DAILY_LIMIT,
            date=format_discord_timestamp(frees),
        )
    )
    assert len(report.channel.tickets) == len(report.notion.created) == 5


async def test_two_modals_opened_at_the_last_report_post_only_one(scenario_factory):
    report = reporting(scenario_factory)
    for _ in range(Commands.REPORT_DAILY_LIMIT - 1):
        await send_report(report)
    await open_modal(report)
    on_the_phone = report.scenario.pending_modal
    await open_modal(report)

    await report.scenario.submit_modal(written(report))
    await report.scenario.submit_modal(written(report), modal=on_the_phone)

    assert len(report.channel.tickets) == len(report.notion.created) == 5
    answer = private_answer(report.scenario)
    assert answer["title"] == copy_text("report.ui.responses.limit.title", "pt-br")


async def test_a_report_goes_through_again_once_the_day_is_over(scenario_factory, deps):
    report = reporting(scenario_factory)
    for _ in range(Commands.REPORT_DAILY_LIMIT):
        await send_report(report)

    deps.redis_client.advance(Commands.REPORT_DAILY_WINDOW_SECONDS)
    await send_report(report)

    assert len(report.channel.tickets) == Commands.REPORT_DAILY_LIMIT + 1


async def test_a_report_the_channel_refused_is_not_counted(
    scenario_factory, deps, analytics_events
):
    report = reporting(scenario_factory)
    report.channel.refusal = refused()

    await send_report(report)

    assert private_answer(report.scenario)["title"].endswith(
        copy_text("errors.generic.title", "pt-br")
    )
    assert report.notion.created == []
    key = Commands.REDIS_REPORT_WINDOW.format(user_id=report.reporter.id)
    assert int(deps.redis_client.get(key) or 0) == 0
    assert [
        event for event in analytics_events if event["type"] == "report.submitted"
    ] == []


async def test_with_redis_down_a_report_goes_through_with_one_warning(
    scenario_factory, deps, monkeypatch, caplog
):
    def down(*_args, **_kwargs):
        raise RedisConnectionError("Timeout reading from redis")

    monkeypatch.setattr(deps.redis_client, "pipeline", down)
    report = reporting(scenario_factory)

    with caplog.at_level("WARNING"):
        await send_report(report)

    assert len(report.channel.tickets) == len(report.notion.created) == 1
    warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "report" in record.getMessage()
    ]
    assert len(warnings) == 1


@pytest.mark.parametrize("status", ["review", "resolved"])
async def test_a_status_click_sets_notion_tells_the_author_and_writes_the_ticket(
    scenario_factory, status
):
    report = reporting(scenario_factory, "en-us")
    await send_report(report)
    [ticket] = report.channel.tickets

    await click_status(report, ticket, status)

    assert report.notion.statuses == [
        (PAGE_ID.replace("-", ""), Commands.REPORT_STATUSES[status])
    ]
    told = report.reporter.dms[-1]
    assert told.title == copy_text(
        "report.ui.responses.status.title", "en-us", ticket="KEIKO-1234"
    )
    assert told.description.startswith(copy_text(STATUS_TEXTS[status], "en-us"))
    written = ticket_fields(ticket)["Status"]
    assert f"by {report.reporter.mention}" in written and written.endswith("DM sent")
    assert ticket.embeds[0].colour.value == STATUS_COLOURS[status]


async def test_the_status_moves_again_on_the_same_ticket(scenario_factory):
    report = reporting(scenario_factory)
    await send_report(report)
    [ticket] = report.channel.tickets

    await click_status(report, ticket, "review")
    await click_status(report, ticket, "resolved")

    statuses = [field for field in ticket.embeds[0].fields if field.name == "Status"]
    assert len(statuses) == 1 and statuses[0].value.startswith("✅ Resolved")
    assert ticket.embeds[0].colour.value == STATUS_COLOURS["resolved"]
    assert len(report.reporter.dms) == 3


@pytest.mark.parametrize("status", ["resolved", "details"])
async def test_an_author_whose_dms_are_closed_still_gets_the_status_on_notion(
    scenario_factory, status
):
    report = reporting(scenario_factory)
    await send_report(report)
    [ticket] = report.channel.tickets
    report.reporter.dms_open = False

    await give_status(report, ticket, status)

    assert len(report.notion.statuses) == 1
    assert ticket_fields(ticket)["Status"].endswith("DM closed, Notion only")


@pytest.mark.parametrize("status", ["review", "details"])
async def test_a_status_notion_refuses_changes_nothing_and_tells_the_operator(
    scenario_factory, status
):
    report = reporting(scenario_factory)
    report.notion.status_answer = no_status_property
    await send_report(report)
    [ticket] = report.channel.tickets
    colour = ticket.embeds[0].colour

    await give_status(report, ticket, status)

    assert {"Status", "Question"}.isdisjoint(ticket_fields(ticket))
    assert ticket.embeds[0].colour == colour
    assert len(report.reporter.dms) == 1, "only the ticket's own DM"
    answer = report.scenario.outputs[-1]
    assert answer["kind"] == "followup_send" and answer["ephemeral"]
    told = answer["embed"]["description"]
    assert "**400**" in told and "Status is not a property that exists." in told
    assert all(name in told for name in Commands.REPORT_STATUSES.values())


async def test_a_status_click_outside_the_admin_server_is_refused(scenario_factory):
    report = reporting(scenario_factory)
    await send_report(report)
    [ticket] = report.channel.tickets
    report.bot.config.ADMIN_GUILD_ID = 1

    await click_status(report, ticket, "resolved")

    assert report.notion.statuses == []
    assert "Status" not in ticket_fields(ticket)
    assert report.scenario.outputs[-1]["kind"] == "send"
    assert report.scenario.outputs[-1]["ephemeral"]


async def test_need_details_first_opens_the_question_and_changes_nothing_yet(
    scenario_factory,
):
    report = reporting(scenario_factory)
    await send_report(report)
    [ticket] = report.channel.tickets

    await click_status(report, ticket, "details")

    assert report.scenario.outputs[-1]["kind"] == "modal"
    assert report.scenario.pending_modal.title == "❓ Ask the author"
    assert report.scenario.pending_modal_fields() == ["Question"]
    [question] = report.scenario.pending_modal_inputs()
    assert (question.style, question.required, question.max_length) == (
        discord.TextStyle.long,
        True,
        Commands.REPORT_TEXT_MAX,
    )
    assert report.notion.statuses == []
    assert "Status" not in ticket_fields(ticket)
    assert len(report.reporter.dms) == 1, "only the ticket's own DM"


async def test_the_question_sets_notion_and_reaches_the_author_quoted_with_a_button(
    scenario_factory, each_locale
):
    report, ticket = await asked(scenario_factory, each_locale)

    assert report.notion.statuses == [
        (PAGE_ID.replace("-", ""), Commands.REPORT_STATUSES["details"])
    ]
    told = report.reporter.dms[-1]
    every_line_quoted = "\n> ".join(QUESTION.splitlines())
    assert told.description.startswith(
        copy_text(STATUS_TEXTS["details"], each_locale, question=every_line_quoted)
    )
    button = answer_button(report.reporter.inbox[-1])
    assert (button.item.label, button.item.style) == (
        ml("report.ui.buttons.answer.label", each_locale),
        discord.ButtonStyle.primary,
    )
    fields = ticket_fields(ticket)
    assert fields["Status"].startswith("❓ Need details")
    assert fields["Status"].endswith("DM sent")
    assert fields["Question"] == QUESTION
    assert ticket.embeds[0].colour.value == STATUS_COLOURS["details"]


async def test_the_answer_button_opens_the_authors_modal_after_a_restart(
    scenario_factory, each_locale
):
    question = "Em qual servidor isso aconteceu?"
    report, _ticket = await asked(scenario_factory, each_locale, question)
    report.scenario.restart()

    await click_answer(report)

    modal = report.scenario.pending_modal
    assert modal.title == answer_text("title", each_locale)
    assert report.scenario.pending_modal_fields() == [
        answer_text("text_label", each_locale)
    ]
    [text] = report.scenario.pending_modal_inputs()
    assert (text.style, text.required, text.max_length) == (
        discord.TextStyle.long,
        True,
        Commands.REPORT_TEXT_MAX,
    )
    [field] = modal.children
    assert field.description == question


async def test_only_the_author_may_answer_and_anyone_else_is_refused_privately(
    scenario_factory,
):
    report, _ticket = await asked(scenario_factory)
    stranger = create_member(report.reporter.guild, id=777, name="Stranger")

    await click_answer(report, by=stranger)

    assert report.scenario.pending_modal is None
    refusal = report.scenario.outputs[-1]
    assert refusal["kind"] == "send" and refusal["ephemeral"]
    assert refusal["embed"]["title"].endswith(
        copy_text("report.ui.errors.not_author.title", "pt-br")
    )


async def test_the_question_opens_a_thread_on_the_ticket_and_is_posted_there(
    scenario_factory,
):
    report, ticket = await asked(scenario_factory)

    thread = thread_of(report, ticket)
    assert thread is not None and thread.name == "#KEIKO-1234"
    [question] = thread.posts
    assert question.embed.title == "❓ Question to the author [#KEIKO-1234]"
    assert question.embed.description == QUESTION
    asker = {field.name: field.value for field in question.embed.fields}["Asked by"]
    assert asker == report.reporter.mention
    pings = question.allowed_mentions.to_dict()
    assert pings == discord.AllowedMentions.none().to_dict()
    assert ticket_fields(ticket)["Question"] == QUESTION
    assert report.channel.replies == []


async def test_a_second_question_is_posted_in_the_same_thread(scenario_factory):
    report, ticket = await asked(scenario_factory)

    await ask_details(report, ticket, "E qual navegador?")

    assert list(report.channel.threads) == [ticket.id]
    first, second = thread_of(report, ticket).posts
    assert (first.embed.description, second.embed.description) == (
        QUESTION,
        "E qual navegador?",
    )
    assert ticket_fields(ticket)["Question"] == "E qual navegador?"


async def test_each_answer_lands_in_the_tickets_thread_and_the_author_is_thanked(
    scenario_factory, each_locale
):
    report, ticket = await asked(scenario_factory, each_locale)

    await answer_question(report, ANSWER)
    await answer_question(report, "E aconteceu de novo hoje")

    _question, first, second = thread_of(report, ticket).posts
    assert report.channel.replies == []
    assert second.embed.description == "E aconteceu de novo hoje"
    assert first.embed.title == "💬 Answer from the author [#KEIKO-1234]"
    assert first.embed.description == ANSWER
    author = {field.name: field.value for field in first.embed.fields}["Author"]
    assert author == report.reporter.mention
    pings = first.allowed_mentions.to_dict()
    assert pings == discord.AllowedMentions.none().to_dict()
    thanks = private_answer(report.scenario)
    assert thanks["title"] == copy_text(
        "report.ui.responses.answered.title", each_locale
    )
    assert thanks["description"].startswith(
        copy_text("report.ui.responses.answered.description", each_locale)
    )
    assert len(report.notion.statuses) == 1, "the status stays need details"


async def test_an_answer_after_a_restart_finds_the_thread_discord_archived(
    scenario_factory,
):
    report, ticket = await asked(scenario_factory)
    thread = thread_of(report, ticket)
    thread.archived = True
    report.scenario.restart()

    await answer_question(report, ANSWER)

    _question, answer = thread.posts
    assert answer.embed.description == ANSWER
    assert thread.archived is False
    assert list(report.channel.threads) == [ticket.id]
    assert report.channel.replies == []


def no_create_public_threads(report):
    report.channel.threads_refused = refused()


def no_send_messages_in_threads(report):
    report.channel.thread_posts_refused = refused()


@pytest.mark.parametrize(
    "refuses",
    [
        pytest.param(no_create_public_threads, id="no-create-public-threads"),
        pytest.param(no_send_messages_in_threads, id="no-send-messages-in-threads"),
    ],
)
async def test_without_threads_the_answer_lands_under_the_ticket_with_one_warning(
    scenario_factory, caplog, refuses
):
    report = reporting(scenario_factory)
    await send_report(report)
    [ticket] = report.channel.tickets
    refuses(report)

    with caplog.at_level("WARNING"):
        await ask_details(report, ticket)
    asking = warnings_logged(caplog)
    caplog.clear()
    with caplog.at_level("WARNING"):
        await answer_question(report, ANSWER)

    assert len(asking) == 1 and len(warnings_logged(caplog)) == 1
    assert ticket_fields(ticket)["Question"] == QUESTION
    assert (thread_of(report, ticket) or SimpleNamespace(posts=[])).posts == []
    [reply] = report.channel.replies
    assert reply.to is ticket and reply.embed.description == ANSWER
    assert private_answer(report.scenario)["title"] == copy_text(
        "report.ui.responses.answered.title", "pt-br"
    )
    assert errors_logged(caplog) == []


def warnings_logged(caplog):
    return [record for record in caplog.records if record.levelname == "WARNING"]


async def the_ticket_is_deleted(report, ticket):
    thread_of(report, ticket).deleted = True
    await ticket.delete()


async def the_channel_refuses(report, ticket):
    report.channel.refusal = refused()


async def the_channel_breaks(report, ticket):
    report.channel.refusal = RuntimeError("the channel broke")


@pytest.mark.parametrize(
    ("breaks", "traceback"),
    [
        pytest.param(the_ticket_is_deleted, False, id="the-ticket-is-gone"),
        pytest.param(the_channel_refuses, False, id="the-channel-refuses"),
        pytest.param(the_channel_breaks, True, id="something-unexpected"),
    ],
)
async def test_an_answer_that_reaches_nobody_tells_the_author_privately(
    scenario_factory, caplog, breaks, traceback
):
    report, ticket = await asked(scenario_factory)
    await breaks(report, ticket)

    with caplog.at_level("ERROR"):
        await answer_question(report)

    assert report.channel.replies == []
    assert len(thread_of(report, ticket).posts) == 1, "only the question"
    answer = private_answer(report.scenario)
    assert answer["title"].endswith(copy_text("errors.generic.title", "pt-br"))
    [error] = errors_logged(caplog)
    assert (error.exc_info is not None) is traceback

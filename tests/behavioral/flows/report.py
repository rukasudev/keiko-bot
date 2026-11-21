"""/reportar: the bot a report reaches (Notion, the admin server's reports channel and
the threads on its tickets, the member's DMs) and the steps around it: open the modal,
send what was written, click a status on the ticket, ask the author a question and
answer it from the DM."""

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import discord
import requests
from discord import app_commands

from app.commands.report.cog import Report
from app.core.utils import ml
from tests.behavioral.harness.fake_interaction import BOT_USER
from tests.mocks.discord import MockMember, create_guild
from tests.mocks.web import respond

REPORTS_CHANNEL_ID = 4100
UPLOAD_CAP = 10 * 1024 * 1024
PAGE_ID = "59833787-2cf9-4fdf-8782-e53db20768a5"
PAGE_URL = "https://www.notion.so/Report-59833787"
PAGES = "https://api.notion.com/v1/pages"
TEXT = "Cliquei em Salvar no /configurar e nada aconteceu"
OTHER = "other"
QUESTION = "Em qual servidor isso aconteceu?\nE o que apareceu na tela depois?"
ANSWER = "No servidor da minha guilda, e a tela ficou parada no card"


def page_created(number: int = 1234, prefix: str = "KEIKO") -> requests.Response:
    """Notion's answer to a new report: the page, its link and its ticket number."""
    body = {
        "id": PAGE_ID,
        "url": PAGE_URL,
        "properties": {"ID": {"unique_id": {"prefix": prefix, "number": number}}},
    }
    return respond(PAGES, json.dumps(body).encode(), content_type="application/json")


def page_without_its_number() -> requests.Response:
    """Notion's answer to a new report on a database with no `ID` property."""
    body = {"id": PAGE_ID, "url": PAGE_URL, "properties": {}}
    return respond(PAGES, json.dumps(body).encode(), content_type="application/json")


def status_set() -> requests.Response:
    """Notion's answer to a status the page now holds."""
    return respond(PAGES, b'{"object": "page"}', content_type="application/json")


def no_status_property() -> requests.Response:
    """Notion's answer while the reports database has no `Status` property."""
    body = (
        b'{"object": "error", "status": 400, "code": "validation_error", '
        b'"message": "Status is not a property that exists."}'
    )
    return respond(PAGES, body, status=400, content_type="application/json")


@dataclass
class Notion:
    """The reports database: what Keiko created and set, answered as the test says."""

    created: List[dict] = field(default_factory=list)
    statuses: List[tuple] = field(default_factory=list)
    answer: Callable[[], Any] = page_created
    status_answer: Callable[[], Any] = status_set

    def create_report(self, **fields):
        self.created.append(fields)
        return self.answer()

    def update_status(self, page_id, name):
        self.statuses.append((page_id, name))
        return self.status_answer()


class ReportsChannel:
    """The admin server's reports channel: each ticket is a message on the scenario's
    screen, the file it carries is kept beside it, and so is each reply under it and
    the thread started from it, by the ticket's id. `refusal` refuses every send,
    `threads_refused` starting a thread (no Create Public Threads) and
    `thread_posts_refused` posting in one (no Send Messages in Threads)."""

    def __init__(self, scenario, guild_id):
        self.scenario = scenario
        self.id = REPORTS_CHANNEL_ID
        self.guild = SimpleNamespace(id=guild_id, filesize_limit=UPLOAD_CAP)
        self.tickets: List[Any] = []
        self.files: List[Optional[discord.File]] = []
        self.replies: List[SimpleNamespace] = []
        self.threads: Dict[int, "TicketThread"] = {}
        self.refusal: Optional[Exception] = None
        self.threads_refused: Optional[Exception] = None
        self.thread_posts_refused: Optional[Exception] = None

    async def send(self, content=None, *, embed=None, view=None, file=None, **kwargs):
        if self.refusal is not None:
            raise self.refusal
        ticket = self.scenario.post(view, embed=embed)
        self.tickets.append(ticket)
        self.files.append(file)
        return ticket

    def get_partial_message(self, message_id):
        return PartialTicket(self, message_id)


class PartialTicket:
    """A message of the reports channel known by its id alone, as discord.py's
    `PartialMessage`: a reply under a ticket that is gone is refused, as Discord does;
    its thread is fetched by an API call, so it is found after a restart and once
    archived, and a second thread on the same ticket is refused."""

    def __init__(self, channel, message_id):
        self.channel = channel
        self.id = message_id

    async def fetch_thread(self):
        thread = self.channel.threads.get(self.id)
        if thread is None or thread.deleted:
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                {"code": 10003, "message": "Unknown Channel"},
            )
        return thread

    async def create_thread(self, *, name, **kwargs):
        if self.channel.threads_refused is not None:
            raise self.channel.threads_refused
        ticket = self._ticket()
        if self.id in self.channel.threads:
            raise discord.HTTPException(
                SimpleNamespace(status=400, reason="Bad Request"),
                {"code": 160004, "message": "A thread has already been created"},
            )
        thread = TicketThread(self.channel, self.id, name, ticket)
        self.channel.threads[self.id] = thread
        return thread

    def _ticket(self):
        ticket = next(
            (ticket for ticket in self.channel.tickets if ticket.id == self.id), None
        )
        if ticket is None or ticket.deleted:
            raise unknown_message()
        return ticket

    async def reply(self, content=None, *, embed=None, allowed_mentions=None, **kwargs):
        if self.channel.refusal is not None:
            raise self.channel.refusal
        ticket = self._ticket()
        message = self.channel.scenario.post(None, embed=embed)
        self.channel.replies.append(
            SimpleNamespace(to=ticket, embed=embed, allowed_mentions=allowed_mentions)
        )
        return message


class TicketThread:
    """The public thread started from a ticket, under the ticket's own id: what Keiko
    posts in it, in order. Discord archives a quiet thread, and a post reopens it."""

    def __init__(self, channel, thread_id, name, ticket):
        self.channel = channel
        self.id = thread_id
        self.name = name
        self.ticket = ticket
        self.posts: List[SimpleNamespace] = []
        self.archived = False
        self.deleted = False

    async def send(self, content=None, *, embed=None, allowed_mentions=None, **kwargs):
        refusal = self.channel.refusal or self.channel.thread_posts_refused
        if refusal is not None:
            raise refusal
        self.archived = False
        message = self.channel.scenario.post(None, embed=embed)
        self.posts.append(SimpleNamespace(embed=embed, allowed_mentions=allowed_mentions))
        return message


def thread_of(report, ticket) -> Optional[TicketThread]:
    """The thread started from `ticket`, if any."""
    return report.channel.threads.get(ticket.id)


def refused(code: int = 50013, message: str = "Missing Permissions") -> discord.Forbidden:
    """What Discord answers a send Keiko may not make."""
    return discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), {"code": code, "message": message}
    )


def unknown_message() -> discord.HTTPException:
    """What Discord answers a reply to a message that was deleted."""
    return discord.HTTPException(
        SimpleNamespace(status=400, reason="Bad Request"),
        {"code": 50035, "message": "Invalid Form Body: Unknown message"},
    )


class Reporter(MockMember):
    """A member who reports: the DMs Keiko sent them, each a message on the scenario's
    screen they may click, or a refusal while they are closed."""

    def __post_init__(self):
        super().__post_init__()
        self.dms: List[discord.Embed] = []
        self.inbox: List[Any] = []
        self.dms_open = True
        self.scenario: Any = None

    async def send(self, content=None, *, embed=None, view=None, **kwargs):
        if not self.dms_open:
            raise refused(50007, "Cannot send messages to this user")
        self.dms.append(embed)
        self.inbox.append(self.scenario.post(view, embed=embed))


class Tree:
    """The command tree the bot syncs: the slash commands a test gives it."""

    translator = None

    def __init__(self, *commands):
        self.commands = list(commands)

    def get_commands(self, *, type=discord.AppCommandType.chat_input):
        return self.commands if type is discord.AppCommandType.chat_input else []


def slash(name: str) -> app_commands.Command:
    """A slash command named `name`, the same in every language."""

    async def answer(interaction):
        return None

    return app_commands.Command(name=name, description=name, callback=answer)


class ReportingBot:
    """The bot a report reaches: Notion, the reports channel, the tree and its users.
    The member's own server is the admin server, so they can click the ticket too."""

    def __init__(self, scenario, reporter, guild_id):
        self.notion = Notion()
        self.channel = ReportsChannel(scenario, guild_id)
        self.reporter = reporter
        self.config = SimpleNamespace(
            ADMIN_REPORTS_CHANNEL_ID=REPORTS_CHANNEL_ID, ADMIN_GUILD_ID=guild_id
        )
        self.tree = Tree()
        self.user = BOT_USER
        self.app_commands: List[Any] = []

    def get_channel(self, channel_id):
        return self.channel if channel_id == REPORTS_CHANNEL_ID else None

    def get_user(self, user_id):
        return None

    async def fetch_user(self, user_id):
        if user_id == self.reporter.id:
            return self.reporter
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 10013, "message": "Unknown User"},
        )


def reporting(scenario_factory, locale: str = "pt-br") -> SimpleNamespace:
    """A member of a server about to report, with the bot their report reaches."""
    guild = create_guild()
    reporter = Reporter(id=555, name="Tester", guild=guild)
    guild.server_members[reporter.id] = reporter
    guild.members.append(reporter)
    scenario = scenario_factory(locale=locale, guild=guild, user=reporter)
    reporter.scenario = scenario
    bot = ReportingBot(scenario, reporter, guild.id)
    scenario.client = bot
    return SimpleNamespace(
        scenario=scenario, bot=bot, notion=bot.notion, channel=bot.channel,
        reporter=reporter, locale=locale,
    )


def modal_text(name: str, locale: str) -> str:
    """A text of the report's modal: its title, a label, a placeholder."""
    return ml(f"report.ui.texts.modal.{name}", locale)


def errors_logged(caplog) -> list:
    """The error logs a step left."""
    return [record for record in caplog.records if record.levelname == "ERROR"]


def answer_text(name: str, locale: str) -> str:
    """A text of the modal the author answers the team in: its title, its label."""
    return ml(f"report.ui.texts.answer_modal.{name}", locale)


async def open_modal(report) -> None:
    """The member runs /reportar."""
    await report.scenario.run_command(Report.report, Report(report.bot))


def written(report, text: str = TEXT, command: str = OTHER,
            file: Optional[str] = None) -> dict:
    """The modal's fields filled with `text`, `command` (an option's label or value)
    and `file` when given, by their labels."""
    fields = {
        modal_text("text_label", report.locale): text,
        modal_text("command_label", report.locale): command,
    }
    if file is not None:
        fields[modal_text("file_label", report.locale)] = file
    return fields


async def send_report(report, text: str = TEXT, command: str = OTHER,
                      file: Optional[str] = None) -> None:
    """The member runs /reportar and sends `text` about `command` (an option's label
    or value), with `file` when given."""
    await open_modal(report)
    await report.scenario.submit_modal(written(report, text, command, file))


def status_button(ticket, status: str) -> discord.ui.DynamicItem:
    """The button of the ticket that sets `status`."""
    return next(
        item for item in ticket.registered_items
        if isinstance(item, discord.ui.DynamicItem)
        and item.custom_id.startswith(f"report:{status}:")
    )


async def click_status(report, ticket, status: str):
    """Someone in the reports channel sets `status` on the ticket, days later, when no
    view holds the ticket any more. Returns the interaction sent."""
    return await report.scenario.click_after_restart(
        status_button(ticket, status), on=ticket
    )


async def ask_details(report, ticket, question: str = QUESTION):
    """Someone in the reports channel clicks Need details on the ticket, days later,
    and asks its author `question`."""
    await click_status(report, ticket, "details")
    await report.scenario.submit_modal({"Question": question})


async def give_status(report, ticket, status: str):
    """Someone in the reports channel gives the ticket `status`, asking its author
    `QUESTION` when the status is need details."""
    if status == "details":
        await ask_details(report, ticket)
    else:
        await click_status(report, ticket, status)


def answer_button(dm) -> discord.ui.DynamicItem:
    """The Answer button under the team's question in the author's DM."""
    return next(
        item for item in dm.registered_items
        if isinstance(item, discord.ui.DynamicItem)
        and item.custom_id.startswith("report:answer:")
    )


async def click_answer(report, *, by=None):
    """Someone clicks Answer on the author's last DM, days later, when no view holds
    it: the author, or `by`. Returns the interaction sent."""
    dm = report.reporter.inbox[-1]
    button = answer_button(dm)
    click = report.scenario.interaction(on=dm, target=button)
    if by is not None:
        click.user = by
    return await report.scenario.click_after_restart(button, on=dm, interaction=click)


async def answer_question(report, text: str = ANSWER):
    """The author clicks Answer on the team's question in their DM, days later, and
    sends `text`."""
    await click_answer(report)
    await report.scenario.submit_modal({answer_text("text_label", report.locale): text})

"""/report: the modal a member reports in, the ticket the team reads in the reports
channel, the copies its author gets by DM, the ticket's status buttons, and the
question the team asks its author in the ticket's thread, which the author answers
from the DM."""

import asyncio
import random
import re
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import discord
from redis.exceptions import RedisError
from requests import RequestException, Response

from app import logger
from app.constants import Commands as constants
from app.constants import DiscordLimits, Style
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.core import analytics
from app.core.app_commands import Listed, listed_commands
from app.core.cache import increment_redis_key_with_expiration, read_redis_counter
from app.core.facts import Facts, facts
from app.core.threads import to_thread
from app.core.utils import (
    find_user,
    format_discord_timestamp,
    inside_quote,
    ml,
    parse_locale,
    postable_file,
    quoted_lines,
    send_dm,
)
from app.integrations.notion import NotionIntegration
from app.ui.buttons import SupportButton
from app.ui.embed import response_embed, response_error_embed


def command_facts(**values: Any) -> Facts:
    """The facts this command's code passes its copy, each under its address."""
    return facts("report", **values)


@dataclass(frozen=True)
class Written:
    """What a member wrote in the report's modal."""

    text: str
    command: str
    command_name: str
    attachment: Any = None


@dataclass(frozen=True)
class Page:
    """The ticket a report opened, and its Notion page when Notion created one."""

    ticket: str
    page_id: str | None = None
    url: str | None = None


async def open_report(interaction: discord.Interaction[Any]) -> None:
    """Open the report's modal, or tell a member who already sent the most reports a
    day takes when they may send another."""
    locale = parse_locale(interaction.locale)
    counted = await _daily_count(interaction.user.id)
    refusal = _limit_reached(counted, locale, interaction.created_at)
    if refusal is not None:
        await interaction.response.send_message(embed=refusal, ephemeral=True)
        return

    listed = await listed_commands(interaction.client.tree, locale)
    await interaction.response.send_modal(
        ReportModal(locale=locale, listed=listed, counted=counted is not None)
    )


class ReportModal(discord.ui.Modal):
    """The modal a member reports in: what happened, in which command, and a file."""

    def __init__(self, *, locale: str, listed: Sequence[Listed], counted: bool) -> None:
        super().__init__(
            title=_modal_text("title", locale)[: DiscordLimits.MODAL_TITLE],
            timeout=view_constants.LONG_TIMEOUT_SECONDS,
        )
        self.locale = locale
        self.counted = counted
        self.text: discord.ui.TextInput[Any] = discord.ui.TextInput(
            style=discord.TextStyle.long,
            placeholder=_modal_text("text_placeholder", locale),
            required=True,
            max_length=constants.REPORT_TEXT_MAX,
        )
        self.command: discord.ui.Select[Any] = discord.ui.Select(
            placeholder=_modal_text("command_placeholder", locale),
            options=command_options(locale=locale, listed=listed),
            required=True,
        )
        self.file: discord.ui.FileUpload[Any] = discord.ui.FileUpload(
            required=False, max_values=1
        )
        self.add_item(_label("text", self.text, locale, described=True))
        self.add_item(_label("command", self.command, locale, described=False))
        self.add_item(_label("file", self.file, locale, described=True))

    async def on_submit(self, interaction: discord.Interaction[Any]) -> None:
        """Send the report to the team and to Notion, and a copy to its author."""
        [command] = self.command.values
        names = {option.value: option.label for option in self.command.options}
        attachments = self.file.values
        written = Written(
            text=self.text.value,
            command=command,
            command_name=names[command],
            attachment=attachments[0] if attachments else None,
        )
        await submit_report(
            interaction, written, locale=self.locale, counted=self.counted
        )

    async def on_error(  # type: ignore[override]
        self, interaction: discord.Interaction[Any], error: Exception, /
    ) -> None:
        """Log what failed on the report and answer its author with the error."""
        await _answer_failure(interaction, error, self.locale, "ReportModal")


def command_options(
    *, locale: str, listed: Sequence[Listed]
) -> list[discord.SelectOption]:
    """Every command a reader is offered, named in `locale` and valued by its English
    name, then the option for something else, at most `DiscordLimits.SELECT_OPTIONS`."""
    commands = [
        discord.SelectOption(
            label=command.name[: DiscordLimits.SELECT_OPTION_TEXT],
            value=command.english[: DiscordLimits.SELECT_OPTION_TEXT],
        )
        for command in listed
    ]
    other = discord.SelectOption(
        label=_modal_text("other_label", locale),
        value="other",
        description=_modal_text("other_description", locale),
    )
    return [*commands[: DiscordLimits.SELECT_OPTIONS - 1], other]


async def submit_report(
    interaction: discord.Interaction[Any],
    written: Written,
    *,
    locale: str,
    counted: bool,
) -> None:
    """Count the report, post the ticket with its file, open its Notion page, then
    tell the author; a member past the day's limit since the modal opened is told so
    instead, and a report that reaches nobody gives its count back."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    user_id = interaction.user.id
    sent = await _count_report(user_id) if counted else None
    refusal = _limit_reached(sent, locale, interaction.created_at)
    if refusal is not None:
        await _give_back(user_id)
        await interaction.followup.send(embed=refusal, ephemeral=True)
        return

    bot: Any = interaction.client
    channel: Any = bot.get_channel(bot.config.ADMIN_REPORTS_CHANNEL_ID)
    posted = await _post_ticket(interaction, channel, written)
    if posted is None:
        if sent is not None:
            await _give_back(user_id)
        embed = response_error_embed("generic", locale)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    message, attachment = posted
    analytics.record(
        "report.submitted",
        guild_id=interaction.guild_id,
        actor=analytics.actor("user", user_id),
        props={"command": written.command},
    )

    page = await _notion_page(interaction, bot, written, message.jump_url)
    ticket = team_ticket(interaction.user, written, attachment, page)
    await _complete_ticket(message, ticket, page, user_id, locale)

    dm_sent = await send_dm(
        interaction.user,
        ticket_embed(
            locale=locale,
            ticket=page.ticket,
            description=written.text,
            command=written.command_name,
            attachment=written.attachment.filename if written.attachment else None,
        ),
    )
    answer = response_embed(
        "report.ui.responses.sent",
        locale,
        discord.Color.green(),
        facts=command_facts(ticket=page.ticket, dm_sent=dm_sent),
    )
    if dm_sent:
        await interaction.followup.send(embed=answer, ephemeral=True)
        return
    support = discord.ui.View()
    support.add_item(SupportButton(locale))
    await interaction.followup.send(embed=answer, view=support, ephemeral=True)


def report_title(text: str) -> str:
    """The first words of a report, at most `Commands.REPORT_TITLE_MAX` characters."""
    title = textwrap.shorten(text, constants.REPORT_TITLE_MAX, placeholder="…")
    if title == "…":
        return f"{text[: constants.REPORT_TITLE_MAX - 1]}…"
    return title


def team_ticket(
    author: Any, written: Written, attachment: str, page: Page | None
) -> discord.Embed:
    """The ticket the team reads in the reports channel, in English."""
    number = f" [#{page.ticket}]" if page else ""
    embed = discord.Embed(
        color=discord.Color.red(), title=f"📩 New ticket created{number}!"
    )
    embed.add_field(name="Title", value=report_title(written.text), inline=False)
    embed.add_field(name="Description", value=written.text, inline=False)
    embed.add_field(name="Command", value=written.command, inline=False)
    embed.add_field(name="Author", value=author.mention)
    embed.add_field(name="Attachment", value=attachment, inline=False)
    if page and page.url:
        embed.add_field(name="Notion Ticket Url", value=page.url, inline=False)
    embed.set_thumbnail(url=author.display_avatar.url)
    return embed


def ticket_embed(
    *,
    locale: str | discord.Locale,
    ticket: str,
    description: str,
    command: str,
    attachment: str | None = None,
) -> discord.Embed:
    """The report as its author receives it, with the name of its file if any."""
    return response_embed(
        "report.ui.responses.ticket",
        locale,
        image=True,
        inline=True,
        facts=command_facts(ticket=ticket),
        fields={
            "command": command,
            "attachment": attachment,
            "description": description,
        },
    )


class ReportStatusButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        rf"report:(?P<status>{'|'.join(constants.REPORT_STATUSES)}):"
        r"(?P<page>[0-9a-f]{32}):(?P<user>\d+):(?P<locale>en-us|pt-br):"
        r"(?P<ticket>[^:]+)"
    ),
):
    """A ticket's status button, which keeps working after a restart: its custom id
    carries the status, the Notion page, the author, their language and the ticket."""

    def __init__(
        self, status: str, page_id: str, user_id: int, locale: str, ticket: str
    ) -> None:
        self.status = status
        self.page_id = page_id
        self.user_id = user_id
        self.locale = locale
        self.ticket = ticket
        emoji, label, style = _status_look(status)
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"report:{status}:{page_id}:{user_id}:{locale}:{ticket}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        _interaction: discord.Interaction[Any],
        _item: discord.ui.Item[Any],
        match: re.Match[str],
    ) -> "ReportStatusButton":
        """The button rebuilt from its custom id, on a ticket no view holds."""
        return cls(
            match["status"],
            match["page"],
            int(match["user"]),
            match["locale"],
            match["ticket"],
        )

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        """Set the status the button names and tell the report's author, or open the
        modal the operator asks the author in."""
        try:
            await set_status(interaction, self)
        except Exception as error:
            await self.on_error(interaction, error)

    async def on_error(
        self, interaction: discord.Interaction[Any], error: Exception
    ) -> None:
        """Log what failed on the ticket and answer the operator with the error."""
        await _answer_failure(interaction, error, "en-us", "ReportStatusButton")


class QuestionModal(discord.ui.Modal):
    """The modal an operator asks a report's author a question in, in English."""

    def __init__(self, button: ReportStatusButton) -> None:
        super().__init__(
            title="❓ Ask the author", timeout=view_constants.LONG_TIMEOUT_SECONDS
        )
        self.button = button
        self.question: discord.ui.TextInput[Any] = discord.ui.TextInput(
            style=discord.TextStyle.long,
            required=True,
            max_length=constants.REPORT_TEXT_MAX,
        )
        self.add_item(
            discord.ui.Label(
                text="Question",
                description="The author gets it by DM as you write it, and reads "
                f"Keiko in {button.locale}.",
                component=self.question,
            )
        )

    async def on_submit(self, interaction: discord.Interaction[Any]) -> None:
        """Set the status on Notion, send the author the question, write it on the
        ticket and post it in the ticket's thread."""
        await interaction.response.defer()
        await _apply_status(interaction, self.button, question=self.question.value)

    async def on_error(  # type: ignore[override]
        self, interaction: discord.Interaction[Any], error: Exception, /
    ) -> None:
        """Log what failed on the question and answer the operator with the error."""
        await _answer_failure(interaction, error, "en-us", "QuestionModal")


class ReportAnswerButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=(
        r"report:answer:(?P<message>\d+):(?P<user>\d+):(?P<locale>en-us|pt-br):"
        r"(?P<ticket>[^:]+)"
    ),
):
    """The Answer button under the team's question in the author's DM, which keeps
    working after a restart: its custom id carries the ticket's message, the author,
    their language and the ticket."""

    def __init__(self, message_id: int, user_id: int, locale: str, ticket: str) -> None:
        self.message_id = message_id
        self.user_id = user_id
        self.locale = locale
        self.ticket = ticket
        super().__init__(
            discord.ui.Button(
                label=ml("report.ui.buttons.answer.label", locale),
                style=discord.ButtonStyle.primary,
                custom_id=f"report:answer:{message_id}:{user_id}:{locale}:{ticket}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        _interaction: discord.Interaction[Any],
        _item: discord.ui.Item[Any],
        match: re.Match[str],
    ) -> "ReportAnswerButton":
        """The button rebuilt from its custom id, on a DM no view holds."""
        return cls(
            int(match["message"]), int(match["user"]), match["locale"], match["ticket"]
        )

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        """Open the modal the author answers the team in."""
        try:
            await open_answer(interaction, self)
        except Exception as error:
            await self.on_error(interaction, error)

    async def on_error(
        self, interaction: discord.Interaction[Any], error: Exception
    ) -> None:
        """Log what failed on the DM and answer its reader with the error."""
        await _answer_failure(interaction, error, self.locale, "ReportAnswerButton")


class AnswerModal(discord.ui.Modal):
    """The modal a report's author answers the team's question in."""

    def __init__(self, button: ReportAnswerButton, *, question: str) -> None:
        super().__init__(
            title=_answer_text("title", button.locale)[: DiscordLimits.MODAL_TITLE],
            timeout=view_constants.LONG_TIMEOUT_SECONDS,
        )
        self.button = button
        self.text: discord.ui.TextInput[Any] = discord.ui.TextInput(
            style=discord.TextStyle.long,
            required=True,
            max_length=constants.REPORT_TEXT_MAX,
        )
        self.add_item(
            discord.ui.Label(
                text=_answer_text("text_label", button.locale)[
                    : DiscordLimits.MODAL_INPUT_LABEL
                ],
                description=_reminder(question),
                component=self.text,
            )
        )

    async def on_submit(self, interaction: discord.Interaction[Any]) -> None:
        """Post the answer in the ticket's thread and thank the author."""
        await answer_team(interaction, self.button, self.text.value)

    async def on_error(  # type: ignore[override]
        self, interaction: discord.Interaction[Any], error: Exception, /
    ) -> None:
        """Log what failed on the answer and answer its author with the error."""
        await _answer_failure(interaction, error, self.button.locale, "AnswerModal")


def report_buttons() -> tuple[type[discord.ui.DynamicItem[Any]], ...]:
    """The items the report routes by their custom id alone."""
    return (ReportStatusButton, ReportAnswerButton)


def status_view(
    *, page_id: str, user_id: int, locale: str, ticket: str
) -> discord.ui.View | None:
    """A ticket's status buttons, one per status `Commands.REPORT_STATUSES` names, or
    None when their custom ids would not fit Discord's limit."""
    buttons = [
        ReportStatusButton(status, page_id, user_id, locale, ticket)
        for status in constants.REPORT_STATUSES
    ]
    if any(len(button.custom_id) > DiscordLimits.CUSTOM_ID for button in buttons):
        return None
    view = discord.ui.View(timeout=None)
    for button in buttons:
        view.add_item(button)
    return view


def answer_view(
    *, message_id: int, user_id: int, locale: str, ticket: str
) -> discord.ui.View:
    """The Answer button under the team's question in the author's DM."""
    view = discord.ui.View(timeout=None)
    view.add_item(ReportAnswerButton(message_id, user_id, locale, ticket))
    return view


async def set_status(
    interaction: discord.Interaction[Any], button: ReportStatusButton
) -> None:
    """Set the report's status on Notion, tell its author by DM and write it on the
    ticket, need details once the operator wrote the question its modal asks; a
    refusal changes nothing and is told to the operator alone."""
    bot: Any = interaction.client
    if str(interaction.guild_id) != str(bot.config.ADMIN_GUILD_ID):
        embed = discord.Embed(
            color=discord.Color.red(),
            title="🚫 Admin server only",
            description="A report's status changes only from the reports channel "
            "of Keiko's admin server.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if button.status == "details":
        await interaction.response.send_modal(QuestionModal(button))
        return
    await interaction.response.defer()
    await _apply_status(interaction, button)


def status_dm(
    *, locale: str, ticket: str, status: str, question: str = ""
) -> discord.Embed:
    """The DM that tells a report's author its new status, with every line of the
    team's question quoted when the team asked one."""
    return response_embed(
        "report.ui.responses.status",
        locale,
        facts=command_facts(
            ticket=ticket, status=status, question=inside_quote(question)
        ),
    )


def with_status(
    ticket: discord.Embed,
    *,
    status: str,
    operator: str,
    at: datetime,
    dm_sent: bool,
    question: str | None = None,
) -> discord.Embed:
    """The ticket in its status's colour, with its Status field written, or written
    again, and the Question field when the team asked one."""
    emoji, label, _style = _status_look(status)
    told = "DM sent" if dm_sent else "DM closed, Notion only"
    value = f"{emoji} {label} · by {operator} · {format_discord_timestamp(at)} · {told}"
    embed = ticket.copy()
    embed.colour = constants.REPORT_STATUS_COLORS[status]
    _write_field(embed, "Status", value)
    if question is not None:
        _write_field(embed, "Question", question)
    return embed


async def open_answer(
    interaction: discord.Interaction[Any], button: ReportAnswerButton
) -> None:
    """Open the modal the report's author answers the team in, the question under
    its field when it fits there; anyone else is refused privately."""
    if interaction.user.id != button.user_id:
        embed = response_error_embed(
            "report.ui.errors.not_author", parse_locale(interaction.locale)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    message = interaction.message
    asked = message.embeds[0].description if message and message.embeds else None
    modal = AnswerModal(button, question=quoted_lines(asked or ""))
    await interaction.response.send_modal(modal)


async def answer_team(
    interaction: discord.Interaction[Any], button: ReportAnswerButton, text: str
) -> None:
    """Post the author's answer in the ticket's thread, or under the ticket when the
    thread is out of Keiko's reach, and thank them privately; an answer that reaches
    nobody gives them the generic error."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    bot: Any = interaction.client
    channel: Any = bot.get_channel(bot.config.ADMIN_REPORTS_CHANNEL_ID)
    answer = team_answer(interaction.user, ticket=button.ticket, text=text)
    posted = await _post_in_thread(
        channel, button.message_id, button.ticket, answer, "it goes under the ticket"
    ) or await _reply_on_ticket(interaction, channel, button.message_id, answer)
    if not posted:
        embed = response_error_embed("generic", button.locale)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    thanks = response_embed(
        "report.ui.responses.answered", button.locale, discord.Color.green()
    )
    await interaction.followup.send(embed=thanks, ephemeral=True)


def team_question(operator: Any, *, ticket: str, text: str) -> discord.Embed:
    """The operator's question as the team reads it in the ticket's thread, in
    English."""
    embed = discord.Embed(
        color=constants.REPORT_STATUS_COLORS["details"],
        title=f"❓ Question to the author [#{ticket}]",
        description=text,
    )
    embed.add_field(name="Asked by", value=operator.mention)
    return embed


def team_answer(author: Any, *, ticket: str, text: str) -> discord.Embed:
    """The author's answer as the team reads it in the ticket's thread, in English."""
    embed = discord.Embed(
        color=discord.Color(int(Style.BACKGROUND_COLOR, 16)),
        title=f"💬 Answer from the author [#{ticket}]",
        description=text,
    )
    embed.add_field(name="Author", value=author.mention)
    return embed


async def _apply_status(
    interaction: discord.Interaction[Any],
    button: ReportStatusButton,
    question: str | None = None,
) -> None:
    bot: Any = interaction.client
    refusal = await _patch_status(bot, button.page_id, button.status)
    if refusal is not None:
        await interaction.followup.send(embed=refusal, ephemeral=True)
        return

    message = interaction.message
    view = None
    if question is not None and message is not None:
        view = answer_view(
            message_id=message.id,
            user_id=button.user_id,
            locale=button.locale,
            ticket=button.ticket,
        )
    dm = status_dm(
        locale=button.locale,
        ticket=button.ticket,
        status=button.status,
        question=question or "",
    )
    dm_sent = await send_dm(await find_user(bot, button.user_id), dm, view=view)
    if message is not None and message.embeds:
        embed = with_status(
            message.embeds[0],
            status=button.status,
            operator=interaction.user.mention,
            at=interaction.created_at,
            dm_sent=dm_sent,
            question=question,
        )
        await interaction.edit_original_response(embed=embed)
    if question is not None and message is not None:
        asked = team_question(interaction.user, ticket=button.ticket, text=question)
        channel = bot.get_channel(bot.config.ADMIN_REPORTS_CHANNEL_ID)
        await _post_in_thread(
            channel, message.id, button.ticket, asked, "it is on the ticket alone"
        )


def _write_field(embed: discord.Embed, name: str, value: str) -> None:
    for index, field in enumerate(embed.fields):
        if field.name == name:
            embed.set_field_at(index, name=name, value=value, inline=False)
            return
    embed.add_field(name=name, value=value, inline=False)


async def _reply_on_ticket(
    interaction: discord.Interaction[Any],
    channel: Any,
    message_id: int,
    embed: discord.Embed,
) -> bool:
    if channel is None:
        _not_posted(
            interaction, "an answer", "the reports channel is not in Keiko's cache"
        )
        return False
    try:
        await channel.get_partial_message(message_id).reply(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.HTTPException as error:
        _not_posted(interaction, "an answer", f"{type(error).__name__} {error.code}")
        return False
    return True


async def _post_in_thread(
    channel: Any, message_id: int, ticket: str, embed: discord.Embed, instead: str
) -> bool:
    if channel is None:
        _not_in_thread(ticket, "the reports channel is not in Keiko's cache", instead)
        return False
    try:
        thread = await _ticket_thread(channel.get_partial_message(message_id), ticket)
        await thread.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as error:
        needs = (
            " (Keiko needs Create Public Threads and Send Messages in Threads there)"
            if isinstance(error, discord.Forbidden)
            else ""
        )
        _not_in_thread(ticket, f"{type(error).__name__} {error.code}{needs}", instead)
        return False
    return True


async def _ticket_thread(message: Any, ticket: str) -> Any:
    try:
        return await message.fetch_thread()
    except discord.NotFound:
        return await message.create_thread(
            name=f"#{ticket}"[: DiscordLimits.THREAD_NAME]
        )


def _not_in_thread(ticket: str, reason: str, instead: str) -> None:
    logger.warn(
        f"Could not post in the thread of ticket {ticket} on the reports channel: "
        f"{reason}, so {instead}",
        log_type=logconstants.COMMAND_WARN_TYPE,
    )


def _label(
    name: str, component: discord.ui.Item[Any], locale: str, *, described: bool
) -> discord.ui.Label[Any]:
    return discord.ui.Label(
        text=_modal_text(f"{name}_label", locale)[: DiscordLimits.MODAL_INPUT_LABEL],
        description=_modal_text(f"{name}_description", locale) if described else None,
        component=component,
    )


def _modal_text(name: str, locale: str) -> str:
    return ml(f"report.ui.texts.modal.{name}", locale)


def _answer_text(name: str, locale: str) -> str:
    return ml(f"report.ui.texts.answer_modal.{name}", locale)


def _reminder(question: str) -> str | None:
    fits = 0 < len(question) <= DiscordLimits.MODAL_INPUT_DESCRIPTION
    return question if fits and "\n" not in question else None


def _status_look(status: str) -> tuple[str, str, discord.ButtonStyle]:
    looks = {
        "review": ("🔎", "In review", discord.ButtonStyle.grey),
        "resolved": ("✅", "Resolved", discord.ButtonStyle.green),
        "details": ("❓", "Need details", discord.ButtonStyle.grey),
    }
    return looks[status]


def _status_refused(answered: str, *, needs_property: bool = False) -> discord.Embed:
    lines = [
        "Nothing changed: the ticket keeps its status and the author got no DM.",
        f"Notion answered {answered}",
    ]
    if needs_property:
        names = constants.REPORT_STATUSES.values()
        options = ", ".join(f"**{name}**" for name in names)
        lines.append(
            "The reports database needs a **Status** property, of the Status type, "
            f"with the options {options}, spelled exactly like that."
        )
    return discord.Embed(
        color=discord.Color.red(),
        title="⚠️ Notion refused the status",
        description="\n\n".join(lines),
    )


async def _patch_status(bot: Any, page_id: str, status: str) -> discord.Embed | None:
    try:
        response = await to_thread(
            bot.notion.update_status, page_id, constants.REPORT_STATUSES[status]
        )
    except RequestException as error:
        return _status_refused(f"nothing in time (`{type(error).__name__}`)")
    if response is None:
        return _status_refused("nothing: the Notion integration is turned off")
    if response.status_code != 200:
        said = NotionIntegration.error_message(response)
        return _status_refused(
            f"**{response.status_code}**: `{said}`",
            needs_property=response.status_code == 400,
        )
    return None


def _limit_reached(
    counted: tuple[int, int] | None, locale: str, now: datetime
) -> discord.Embed | None:
    if counted is None:
        return None
    count, seconds = counted
    if count < constants.REPORT_DAILY_LIMIT or seconds <= 0:
        return None
    return response_embed(
        "report.ui.responses.limit",
        locale,
        facts=command_facts(
            count=constants.REPORT_DAILY_LIMIT,
            date=format_discord_timestamp(now + timedelta(seconds=seconds)),
        ),
    )


def _daily_key(user_id: int) -> str:
    return constants.REDIS_REPORT_WINDOW.format(user_id=user_id)


async def _daily_count(user_id: int) -> tuple[int, int] | None:
    try:
        return await asyncio.wait_for(
            to_thread(read_redis_counter, _daily_key(user_id)),
            view_constants.READ_BEFORE_ANSWER_SECONDS,
        )
    except TimeoutError:
        logger.warn(
            "Redis did not say in time how many reports a member sent today, so the "
            "modal opens and the report is counted when sent",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return (0, 0)
    except RedisError as error:
        logger.warn(
            "Could not read how many reports a member sent today, so the report goes "
            f"through: {type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None


async def _count_report(user_id: int) -> tuple[int, int] | None:
    try:
        return await to_thread(_counted, _daily_key(user_id))
    except RedisError as error:
        logger.warn(
            f"Could not count a member's report, so it goes through: "
            f"{type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None


def _counted(key: str) -> tuple[int, int]:
    count = increment_redis_key_with_expiration(
        key, 1, constants.REPORT_DAILY_WINDOW_SECONDS
    )
    _count, seconds = read_redis_counter(key)
    return int(count) - 1, seconds


async def _give_back(user_id: int) -> None:
    try:
        await to_thread(
            increment_redis_key_with_expiration,
            _daily_key(user_id),
            -1,
            constants.REPORT_DAILY_WINDOW_SECONDS,
        )
    except RedisError as error:
        logger.warn(
            "Could not give back the count of a report that reached nobody: "
            f"{type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


async def _answer_failure(
    interaction: discord.Interaction[Any], error: Exception, locale: str, where: str
) -> None:
    logger.error(
        f"{where} error: {type(error).__name__}: {error}",
        interaction=interaction,
        log_type=logconstants.COMMAND_ERROR_TYPE,
        exc_info=error,
    )
    embed = response_error_embed("generic", locale)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException as answer_error:
        logger.warn(
            f"Could not tell someone {where} failed: {type(answer_error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


async def _post_ticket(
    interaction: discord.Interaction[Any], channel: Any, written: Written
) -> tuple[Any, str] | None:
    if channel is None:
        _not_posted(
            interaction, "a report", "the reports channel is not in Keiko's cache"
        )
        return None
    file = await postable_file(written.attachment, channel)
    attachment = _attachment_line(written.attachment, file)
    sent: dict[str, Any] = {
        "embed": team_ticket(interaction.user, written, attachment, None)
    }
    if file is not None:
        sent["file"] = file
    try:
        message = await channel.send(**sent)
    except discord.HTTPException as error:
        _not_posted(interaction, "a report", type(error).__name__)
        return None
    return message, attachment


def _not_posted(interaction: discord.Interaction[Any], what: str, reason: str) -> None:
    logger.error(
        f"Could not post {what} on the reports channel: {reason}",
        interaction=interaction,
        log_type=logconstants.COMMAND_ERROR_TYPE,
    )


def _attachment_line(attachment: Any, file: discord.File | None) -> str:
    if attachment is None:
        return "No attachment"
    if file is not None:
        return str(attachment.filename)
    return f"{attachment.url} (Discord's link, it stops working in a day)"


async def _notion_page(
    interaction: discord.Interaction[Any], bot: Any, written: Written, link: str
) -> Page:
    try:
        response = await to_thread(
            bot.notion.create_report,
            title=report_title(written.text),
            description=written.text,
            command=written.command,
            author=str(interaction.user.id),
            attachment_url=link if written.attachment else None,
        )
    except RequestException as error:
        logger.error(
            f"Error creating report in Notion: {type(error).__name__}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
        response = None

    created = _created_page(response) if response is not None else None
    if created is not None:
        return created
    if response is not None and response.status_code == 200:
        logger.error(
            "Error creating report in Notion: the page it created has no ticket "
            "number (its `ID` property)",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
    elif response is not None:
        logger.error(
            "Error creating report in Notion "
            f"(status_code: {response.status_code}): {response.text[:200]}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
    return Page(ticket=f"KEIKO-{random.randint(1000, 9999)}")


def _created_page(response: Response) -> Page | None:
    if response.status_code != 200:
        return None
    try:
        body = response.json()
        unique = body["properties"]["ID"]["unique_id"]
        return Page(
            ticket=f"{unique['prefix']}-{unique['number']}",
            page_id=str(body["id"]).replace("-", ""),
            url=body.get("url"),
        )
    except (ValueError, KeyError, TypeError):
        return None


async def _complete_ticket(
    message: Any, embed: discord.Embed, page: Page, user_id: int, locale: str
) -> None:
    edits: dict[str, Any] = {"embed": embed}
    if page.page_id is not None:
        view = status_view(
            page_id=page.page_id, user_id=user_id, locale=locale, ticket=page.ticket
        )
        if view is not None:
            edits["view"] = view
    try:
        await message.edit(**edits)
    except discord.HTTPException as error:
        logger.warn(
            f"Could not write the ticket number {page.ticket} on a report: "
            f"{type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )

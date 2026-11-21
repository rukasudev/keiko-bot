"""The /help command list, for the slash command and the buttons that open it, Keiko's
own prefix commands, and the reaction to a message that mentions Keiko."""

import contextlib
import random
from typing import Any, Callable, Dict, List, Optional, Sequence

import discord

from app.bot import DiscordBot
from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.core import analytics, i18n
from app.core.app_commands import Listed, listed_commands
from app.core.commands import PARENT_GROUPS, MessageSent, ParentGroup, opener
from app.core.facts import Facts, facts
from app.core.servers import interaction_language, server_language
from app.core.utils import fill, is_guild_admin, ml, parse_locale
from app.forms import run_feature_command
from app.forms.features import feature_keys
from app.ui.buttons import BackButton, GenericButton
from app.ui.embed import response_embed
from app.ui.pagination import PageSelect, PaginationView


def command_facts(**values: Any) -> Facts:
    """The facts this command's code passes its copy, each under its address."""
    return facts("help", **values)


def _group_heading(group: ParentGroup, locale: str) -> str:
    return fill(
        ml("help.ui.records.commands.lines.group", locale),
        emoji=PARENT_GROUPS[group],
        name=ml(f"texts.groups.{group}", locale).capitalize(),
    )


def command_fields(
    listed: Sequence[Listed], locale: str, configures: bool
) -> Dict[str, List[Listed]]:
    """The commands a reader is shown, by the title of the field that lists them: the
    slash commands everyone can run, the message menus the reader can run, then, for
    whoever configures Keiko, what configures it outside a group and each parent
    group in /setup's order."""
    fields = {
        ml("help.ui.records.commands.lines.general", locale): [
            command for command in listed if not command.configures and not command.menu
        ],
        ml("help.ui.records.commands.lines.menus", locale): [
            command
            for command in listed
            if command.menu and (configures or not command.configures)
        ],
    }
    if configures:
        groups = {
            group: [command for command in listed if command.group == group]
            for group in PARENT_GROUPS
        }
        fields[ml("help.ui.records.commands.lines.setup", locale)] = [
            command
            for command in listed
            if command.configures and command.group is None and not command.menu
        ]
        for group, commands in groups.items():
            fields[_group_heading(group, locale)] = commands
    return {
        title: sorted(commands, key=lambda command: command.name)
        for title, commands in fields.items()
        if commands
    }


def command_line(command: Listed, locale: str) -> str:
    """A command's line on the list: its slash path, or a menu by its label."""
    line = "message_command" if command.menu else "slash_command"
    return fill(ml(f"help.ui.records.commands.lines.{line}", locale), name=command.name)


def field_text(commands: Sequence[Listed], locale: str) -> str:
    """A field's lines, one per command, under how to reach them when they are
    message menus."""
    lines = [command_line(command, locale) for command in commands]
    if all(command.menu for command in commands):
        lines.insert(0, ml("help.ui.records.commands.lines.menus_path", locale))
    return "\n".join(lines)


class ExecuteCommandButton(discord.ui.Button[discord.ui.View]):
    """The button that opens the feature of the command the help describes."""

    def __init__(self, command_key: str, locale: str) -> None:
        self.command_key = command_key
        label = ml("help.ui.buttons.execute.label", locale=locale)
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the feature for whoever holds Administrator or Manage Server, refuse
        anyone else."""
        locale = await interaction_language(interaction)
        if not is_guild_admin(interaction.user):
            embed = response_embed("commands.responses.admin_only", locale)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await run_feature_command(interaction, self.command_key, "help_button")


class HelpSelect(PageSelect):
    """The commands of the page on screen, each opening its description."""

    def __init__(self, placeholder: str, data: Dict[str, List[Listed]]) -> None:
        self.data = data
        super().__init__(
            placeholder=placeholder,
            options={},
            custom_callback=self.after_callback,
        )

    def follow(self, keys: Sequence[str]) -> None:
        """Offer the commands of the page whose titles are `keys`, one at a time."""
        self.options = self.parse_options({name: name for name in self._commands(keys)})
        self.max_values = 1

    def _commands(self, keys: Sequence[str]) -> Dict[str, Listed]:
        return {command.name: command for item in keys for command in self.data[item]}

    async def after_callback(self, interaction: discord.Interaction) -> None:
        """Describe the chosen command, with a way back and a way to open it."""
        view = self.view
        if view is None or interaction.message is None:
            return
        embed = interaction.message.embeds[0]
        locale = parse_locale(interaction.locale)

        detail = view.describe(
            ml("help.ui.records.commands.detail.title", locale),
            ml("help.ui.records.commands.detail.description", locale),
        )

        command = self._commands(view.get_current_page_data())[self.values[0]]

        detail.add_field(
            name=fill(
                ml("help.ui.records.commands.lines.picked", locale), name=command.name
            ),
            value=command.description or field_text([command], locale),
            inline=False,
        )

        new_view = discord.ui.View(timeout=view_constants.LONG_TIMEOUT_SECONDS)
        new_view.add_item(BackButton(embed=embed, view=view, locale=locale))

        if command.key is not None and command.key in feature_keys():
            new_view.add_item(
                ExecuteCommandButton(command_key=command.key, locale=locale)
            )

        await interaction.response.edit_message(embed=detail, view=new_view)


async def send_help(
    interaction: discord.Interaction[DiscordBot], source: Optional[str] = None
) -> None:
    """Answer the paged command list privately, with its command select, one field a
    page for a member and two for whoever configures Keiko, who also gets the
    Configurar button."""
    analytics.record(
        "help.opened",
        guild_id=interaction.guild_id,
        actor=analytics.actor("user", interaction.user.id),
        props={"source": source or analytics.resolve_source(interaction)},
    )
    locale = parse_locale(interaction.locale)
    configures = is_guild_admin(interaction.user)
    copy = i18n.resolved_texts(
        "help.ui.records.commands", command_facts(configures=configures), locale
    )
    listed = await listed_commands(interaction.client.tree, locale)
    fields = command_fields(listed, locale, configures)
    view = PaginationView(
        interaction,
        copy["title"],
        copy["description"],
        {title: field_text(commands, locale) for title, commands in fields.items()},
        sep=(
            view_constants.HELP_PAGE_SIZE
            if configures
            else view_constants.MEMBER_HELP_PAGE_SIZE
        ),
    )
    view.add_select(HelpSelect(copy["placeholder"], fields), first=True)
    if configures:
        view.add_item(_setup_button(locale))
    await view.send(ephemeral=True)


def _setup_button(locale: str) -> GenericButton:
    async def open_dashboard(interaction: discord.Interaction) -> None:
        await opener("setup")(interaction, "help_button")

    return GenericButton(
        ml("setup.ui.buttons.dashboard.label", locale),
        open_dashboard,
        discord.ButtonStyle.primary,
        emoji="🔧",
    )


def ping_embed(bot: DiscordBot, locale: str | discord.Locale) -> discord.Embed:
    """The /ping answer: Pong and Keiko's gateway latency in ms."""
    return response_embed(
        "help.ui.responses.ping",
        locale,
        facts=command_facts(ping=round(bot.latency * 1000)),
    )


def _own_command(
    content: str, prefix: str
) -> Optional[Callable[[DiscordBot, str], discord.Embed]]:
    answers = {"ping": ping_embed}
    return answers.get(content[len(prefix) :].split(" ")[0])


async def answer_prefix_command(
    bot: DiscordBot, guild_id: str, message: discord.Message, prefix: str
) -> bool:
    """Answer one of Keiko's own prefix commands in the server's language, True when
    the message was one."""
    answer = _own_command(message.content, prefix)
    if answer is None:
        return False
    locale = await server_language(guild_id)
    await message.reply(embed=answer(bot, locale))
    return True


async def answer_own_prefix(sent: MessageSent) -> bool:
    """Answer a message on the server's prefix that calls one of Keiko's own
    commands, True when it was one."""
    from app import bot

    if sent.guild_id is None:
        return False
    return await answer_prefix_command(bot, sent.guild_id, sent.message, sent.prefix)


async def react_to_mention(sent: MessageSent) -> None:
    """Put Keiko's reaction on a message in a server whose text mentions Keiko or
    Keiko's own role and, once in `Commands.MENTION_WOOF_CHANCE`, a bark in reply; a
    channel where Keiko may not react gets nothing."""
    from app import bot

    message = sent.message
    if sent.guild_id is None or bot.user is None:
        return
    role = message.guild.self_role
    if bot.user.id not in message.raw_mentions and (
        role is None or role.id not in message.raw_role_mentions
    ):
        return
    try:
        await message.add_reaction(Commands.MENTION_REACTION)
    except discord.HTTPException:
        return
    if random.random() < Commands.MENTION_WOOF_CHANCE:
        await _bark(message, sent.guild_id)


async def _bark(message: discord.Message, guild_id: str) -> None:
    bark = random.choice(("once", "twice"))
    text = ml(f"help.ui.texts.woof.{bark}", await server_language(guild_id))
    with contextlib.suppress(discord.HTTPException):
        await message.reply(text, mention_author=False)

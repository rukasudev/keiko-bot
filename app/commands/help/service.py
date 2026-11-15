"""The /help command list, for the slash command and the buttons that open it, Keiko's
own prefix commands, and the hint a prefixed message gets when no feature answers the
prefix."""

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypedDict

import discord
from discord import app_commands

from app.bot import DiscordBot
from app.constants import ViewConstants as view_constants
from app.core import analytics, cache
from app.core.commands import Command as KeikoCommand
from app.core.commands import MessageSent, opener, setup_features
from app.core.servers import (
    default_prefix,
    interaction_language,
    server_language,
    server_prefix,
)
from app.core.utils import (
    fill,
    get_command_display_name,
    is_guild_admin,
    ml,
    parse_locale,
    parse_valid_locale,
)
from app.forms import run_feature_command
from app.forms.features import feature_keys
from app.ui.buttons import BackButton, GenericButton
from app.ui.embed import response_embed
from app.ui.pagination import PageSelect, PaginationView

AppCommand = app_commands.Command[Any, ..., Any] | app_commands.Group


class CommandInfo(TypedDict):
    """A command as the help select offers it."""

    name: str
    description: str
    command_key: Optional[str]


def _is_guild_command(
    interaction: discord.Interaction[DiscordBot], command: AppCommand
) -> bool:
    if interaction.guild_id == interaction.client.config.ADMIN_GUILD_ID:
        return False
    parent = command
    if command.parent:
        parent = command.parent.parent if command.parent.parent else command.parent
    return parent.name not in ("notifications", "moderations", "help")


def _command_info(
    interaction: discord.Interaction[DiscordBot], command: AppCommand
) -> CommandInfo:
    locale = parse_valid_locale(interaction.locale).value
    return {
        "name": (
            command.extras[locale]["locale_qualified_name"]
            if command.extras
            else command.qualified_name
        ),
        "description": (
            command.extras[locale]["locale_qualified_desc"]
            if command.extras
            else command.description
        ),
        "command_key": getattr(command, "_attr", None),
    }


def _group_title(base_title: str, command: AppCommand, locale: str) -> str:
    if command.parent is None:
        return base_title
    if not command.extras:
        return command.qualified_name.split()[0]
    name: str = command.extras[locale]["locale_qualified_name"]
    return name.split()[0]


def command_list(
    interaction: discord.Interaction[DiscordBot],
) -> Tuple[Dict[str, str], Dict[str, List[CommandInfo]]]:
    """The commands grouped by title, as pagination fields and as select data."""
    base_title = ml("help.records.commands.lines.base_commands", interaction.locale)
    locale = parse_valid_locale(interaction.locale).value
    lines: Dict[str, List[str]] = {}
    select_data: Dict[str, List[CommandInfo]] = {}

    for command in interaction.client.app_commands:
        if _is_guild_command(interaction, command):
            continue
        info = _command_info(interaction, command)
        title = _group_title(base_title, command, locale).capitalize()
        lines.setdefault(title, []).append(f":flying_disc: `/{info['name']}`")
        select_data.setdefault(title, []).append(info)

    for title in lines:
        select_data[title].sort(key=lambda info: info["name"])
        lines[title].sort()
    fields = {title: "\n".join(lines[title]) for title in sorted(lines)}
    return fields, select_data


class ExecuteCommandButton(discord.ui.Button[discord.ui.View]):
    """The button that opens the feature of the command the help describes."""

    def __init__(self, command_key: str, locale: str) -> None:
        self.command_key = command_key
        label = ml("help.buttons.execute.label", locale=locale)
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the feature for an administrator, refuse anyone else."""
        locale = await interaction_language(interaction)
        if not is_guild_admin(interaction.user):
            embed = response_embed("commands.responses.admin_only", locale)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await run_feature_command(interaction, self.command_key, "help_button")


class HelpSelect(PageSelect):
    """The commands of the page on screen, each opening its description."""

    def __init__(self, placeholder: str, data: Dict[str, List[CommandInfo]]) -> None:
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

    def _commands(self, keys: Sequence[str]) -> Dict[str, CommandInfo]:
        return {
            command["name"]: command for item in keys for command in self.data[item]
        }

    async def after_callback(self, interaction: discord.Interaction) -> None:
        """Describe the chosen command, with a way back and a way to open it."""
        view = self.view
        if view is None or interaction.message is None:
            return
        embed = interaction.message.embeds[0]

        detail = view.describe(
            ml("help.records.commands.detail.title", interaction.locale),
            ml("help.records.commands.detail.description", interaction.locale),
        )

        selected = self.values[0]
        command_data = self._commands(view.get_current_page_data())[selected]

        detail.add_field(
            name=f"✨ /{selected}",
            value=command_data["description"],
            inline=False,
        )

        locale = str(interaction.locale)
        new_view = discord.ui.View(timeout=view_constants.LONG_TIMEOUT_SECONDS)
        new_view.add_item(BackButton(embed=embed, view=view, locale=interaction.locale))

        command_key = command_data["command_key"]
        if command_key is not None and command_key in feature_keys():
            new_view.add_item(
                ExecuteCommandButton(command_key=command_key, locale=locale)
            )

        await interaction.response.edit_message(embed=detail, view=new_view)


async def send_help(
    interaction: discord.Interaction[DiscordBot],
    source: Optional[str] = None,
    ephemeral: bool = False,
) -> None:
    """Open the paginated command list with its command select."""
    analytics.record(
        "help.opened",
        guild_id=interaction.guild_id,
        actor=analytics.actor("user", interaction.user.id),
        props={"source": source or analytics.resolve_source(interaction)},
    )
    fields, select_data = command_list(interaction)
    base = "help.records.commands"
    view = PaginationView(
        interaction,
        ml(f"{base}.title", locale=interaction.locale),
        ml(f"{base}.description", locale=interaction.locale),
        fields,
    )
    view.add_select(
        HelpSelect(ml(f"{base}.placeholder", locale=interaction.locale), select_data),
        first=True,
    )
    view.add_item(_setup_button(parse_locale(interaction.locale)))
    await view.send(ephemeral=ephemeral)


async def open_help(interaction: discord.Interaction[DiscordBot], source: str) -> None:
    """Open the command list privately from a button on another screen."""
    await send_help(interaction, source, ephemeral=True)


def _setup_button(locale: str) -> GenericButton:
    async def open_dashboard(interaction: discord.Interaction) -> None:
        await opener("setup")(interaction, "help_button")

    return GenericButton(
        ml("setup.buttons.dashboard.label", locale),
        open_dashboard,
        discord.ButtonStyle.primary,
        emoji="🔧",
    )


def prefix_features() -> List[KeikoCommand]:
    """The `/setup` features that answer the configured prefix."""
    return [command for command in setup_features() if command.answers_prefix]


async def any_prefix_feature_on(guild_id: str) -> bool:
    """Whether a feature that answers the prefix is on in the guild."""
    for spec in prefix_features():
        if await cache.read_cog_data(guild_id, spec.key):
            return True
    return False


async def prefix_hint(bot: DiscordBot, guild: Optional[discord.Guild]) -> Optional[str]:
    """What Keiko answers a prefixed message no text command matched, if anything."""
    if guild is None:
        return "\n\n".join(
            ml("help.messages.prefix_hint.direct", locale)
            for locale in (
                discord.Locale.brazil_portuguese,
                discord.Locale.american_english,
            )
        )
    if await any_prefix_feature_on(str(guild.id)):
        return None

    locale = await server_language(str(guild.id))
    activations = "\n".join(
        fill(
            ml("help.messages.prefix_hint.feature", locale),
            command=get_command_display_name(bot, spec.key, locale),
        )
        for spec in prefix_features()
    )
    return fill(
        ml("help.messages.prefix_hint.guild", locale),
        prefix=await server_prefix(str(guild.id)),
        commands=activations,
    )


def ping_embed(bot: DiscordBot, locale: str | discord.Locale) -> discord.Embed:
    """The /ping answer: Pong and Keiko's gateway latency in ms."""
    return response_embed(
        "help.responses.ping", locale, values={"ping": round(bot.latency * 1000)}
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


async def send_prefix_hint(sent: MessageSent) -> None:
    """Tell a message on the default prefix that no command of Keiko answered how to
    reach the commands that answer one."""
    from app import bot

    if sent.prefix_handled or not sent.content.startswith(default_prefix()):
        return
    hint = await prefix_hint(bot, sent.message.guild)
    if hint:
        await sent.message.channel.send(hint)

"""The /help command list, for the slash command and the buttons that open it."""
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.app_commands.commands import Command

from app.components.select import HelpSelect
from app.constants import CogsConstants as cogs_constants
from app.services import analytics
from app.services.utils import ml, parse_valid_locale
from app.views.pagination import PaginationView


def _is_guild_command(interaction: discord.Interaction, command: Command) -> bool:
    if interaction.guild.id == interaction.client.config.ADMIN_GUILD_ID:
        return False
    parent = command
    if command.parent:
        parent = command.parent.parent if command.parent.parent else command.parent
    return parent.name not in cogs_constants.INTERACTION_COGS


def _command_info(interaction: discord.Interaction, command: Command) -> Dict[str, Any]:
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


def _group_title(base_title: str, command: Command, locale: str) -> str:
    if command.parent is None:
        return base_title
    if not command.extras:
        return command.qualified_name.split()[0]
    return command.extras[locale]["locale_qualified_name"].split()[0]


def command_list(
    interaction: discord.Interaction,
) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]]]:
    """The commands grouped by title, as pagination fields and as select data."""
    base_title = ml("commands.commands.help.embed.base-commands", interaction.locale)
    locale = parse_valid_locale(interaction.locale).value
    lines: Dict[str, List[str]] = {}
    select_data: Dict[str, List[Dict[str, Any]]] = {}

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


async def send_help(
    interaction: discord.Interaction,
    source: Optional[str] = None,
    ephemeral: bool = False,
) -> None:
    """Open the paginated command list with its command select."""
    analytics.emit(
        "help.opened",
        guild_id=interaction.guild_id,
        user_id=interaction.user.id,
        source=source or analytics.resolve_source(interaction),
    )
    fields, select_data = command_list(interaction)
    base = "commands.commands.help.embed"
    view = PaginationView(
        interaction,
        ml(f"{base}.title", locale=interaction.locale),
        ml(f"{base}.desc", locale=interaction.locale),
        fields,
    )
    view.add_select(
        HelpSelect(ml(f"{base}.placeholder", locale=interaction.locale), select_data),
        first=True,
    )
    await view.send(ephemeral=ephemeral)

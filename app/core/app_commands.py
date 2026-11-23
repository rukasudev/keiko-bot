"""The app commands Keiko offers in every server, as a reader sees them: each slash
command to its leaf and each message menu, marked for everyone or for whoever configures
Keiko."""

from dataclasses import dataclass
from typing import Any, cast

import discord
from discord import app_commands

from app.core.commands import PARENT_GROUPS, ParentGroup, commands
from app.core.utils import parse_valid_locale

ENGLISH = discord.Locale.american_english


@dataclass(frozen=True)
class Listed:
    """One app command a reader can run: its name in the reader's language and in
    English, its caption, the key of the command a subgroup's leaf opens (the one whose
    cog declares the leaf, in a subgroup it may share), the parent group it sits under,
    and whether it is for whoever configures Keiko."""

    name: str
    english: str
    description: str
    key: str | None
    menu: bool
    configures: bool
    group: ParentGroup | None = None


async def listed_commands(
    tree: app_commands.CommandTree, locale: Any
) -> tuple[Listed, ...]:
    """Every global app command of `tree` in `locale`: the slash commands to their
    leaves, then the message menus, in the order the tree holds them."""
    reader = parse_valid_locale(locale)
    slash = [
        _slash(command, reader)
        for root in tree.get_commands(type=discord.AppCommandType.chat_input)
        for command in _leaves(root)
    ]
    menus = [
        await _menu(menu, tree, reader)
        for menu in tree.get_commands(type=discord.AppCommandType.message)
        if isinstance(menu, app_commands.ContextMenu)
    ]
    return (*slash, *menus)


def _leaves(root: Any) -> list[app_commands.Command[Any, ..., Any]]:
    if isinstance(root, app_commands.Group):
        return [
            command
            for command in root.walk_commands()
            if isinstance(command, app_commands.Command)
        ]
    return [root] if isinstance(root, app_commands.Command) else []


def _slash(
    command: app_commands.Command[Any, ..., Any], reader: discord.Locale
) -> Listed:
    path = command.qualified_name
    group = _group(command)
    return Listed(
        name=f"/{_extra(command, reader, 'locale_qualified_name', path)}",
        english=f"/{_extra(command, ENGLISH, 'locale_qualified_name', path)}",
        description=_extra(
            command, reader, "locale_qualified_desc", command.description
        ),
        key=_declared_by(command),
        menu=False,
        configures=group is not None or _gated(command),
        group=group,
    )


def _declared_by(command: app_commands.Command[Any, ..., Any]) -> str | None:
    if command.parent is None:
        return None
    cog = type(command.binding)
    return next((declared.key for declared in commands() if cog in declared.cogs), None)


def _extra(
    command: app_commands.Command[Any, ..., Any],
    locale: discord.Locale,
    name: str,
    default: str,
) -> str:
    translated = (command.extras.get(locale.value) or {}).get(name)
    return str(translated) if translated else default


def _group(command: app_commands.Command[Any, ..., Any]) -> ParentGroup | None:
    root = command.root_parent or command.parent
    if root is None or root.name not in PARENT_GROUPS:
        return None
    return cast(ParentGroup, root.name)


def _gated(command: app_commands.Command[Any, ..., Any]) -> bool:
    return any(
        node is not None and node.default_permissions is not None
        for node in (command, command.parent, command.root_parent)
    )


async def _menu(
    menu: app_commands.ContextMenu,
    tree: app_commands.CommandTree,
    reader: discord.Locale,
) -> Listed:
    translator = tree.translator
    payload = await menu.get_translated_payload(tree, translator) if translator else {}
    names = payload.get("name_localizations") or {}
    return Listed(
        name=names.get(reader.value, menu.name),
        english=names.get(ENGLISH.value, menu.name),
        description="",
        key=None,
        menu=True,
        configures=menu.default_permissions is not None,
    )

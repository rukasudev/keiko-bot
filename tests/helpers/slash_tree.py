"""The slash tree the bot syncs at startup, built offline with `app/translator.py`:
the payload Discord receives for each app command, the class each slash command is
bound to, and the path a member types for each command and group, in each locale."""

import asyncio
from dataclasses import dataclass
from importlib import import_module
from typing import Any
from unittest.mock import MagicMock

import discord
from discord import app_commands
from discord.ext import commands as ext
from discord.ext import tasks

from app.core.utils import startup_extensions
from app.translator import Translator

DISCORD_LOCALES = {"en-us": "en-US", "pt-br": "pt-BR"}
SLASH = discord.AppCommandType.chat_input.value
NESTED = {
    discord.AppCommandOptionType.subcommand.value,
    discord.AppCommandOptionType.subcommand_group.value,
}
_BUILT: list["SyncedTree"] = []


@dataclass(frozen=True)
class SyncedTree:
    """What the bot sends Discord at startup, read once the tree is built."""

    payloads: list[dict[str, Any]]
    owners: dict[str, type]

    def paths(self) -> dict[str, dict[str, str]]:
        """Every slash command and group by its qualified name, with the path a member
        types for it in each locale (`/moderations default roles`)."""
        found: dict[str, dict[str, str]] = {}
        for payload in self.payloads:
            if payload.get("type", SLASH) == SLASH:
                _walk(payload, "", {locale: "" for locale in DISCORD_LOCALES}, found)
        return found

    def typed(self, locale: str) -> set[str]:
        """Every path a member can type in `locale`, a group's included."""
        return {path[locale] for path in self.paths().values()}

    def path_of(self, cogs: tuple[type, ...], locale: str) -> list[str]:
        """The path in `locale` of every slash command bound to one of `cogs`."""
        paths = self.paths()
        return sorted(
            paths[name][locale]
            for name, owner in self.owners.items()
            if issubclass(owner, cogs)
        )


async def synced_tree() -> SyncedTree:
    """The tree, built once per test run and only read afterwards."""
    if not _BUILT:
        _BUILT.append(await built_tree([]))
    return _BUILT[0]


async def built_tree(loops: list) -> SyncedTree:
    """A fresh build of the tree of every startup extension, translated; the cogs'
    loops land in `loops`."""
    async with ext.Bot(
        command_prefix="!", intents=discord.Intents.default(), help_command=None
    ) as bot:
        return await _translated(bot, loops)


async def _translated(bot: ext.Bot, loops: list) -> SyncedTree:
    bot.all_cogs = {}
    bot.app_commands = []
    for name in ("twitch", "youtube", "reminder", "notion", "config"):
        setattr(bot, name, MagicMock())
    for extension in startup_extensions():
        await import_module(extension).setup(bot)
    translator = Translator(bot)
    await bot.tree.set_translator(translator)
    try:
        payloads = [
            await command.get_translated_payload(bot.tree, translator)
            for command in bot.tree.get_commands()
        ]
        owners = {
            command.qualified_name: type(command.binding)
            for command in bot.tree.walk_commands()
            if isinstance(command, app_commands.Command) and command.binding is not None
        }
        return SyncedTree(payloads, owners)
    finally:
        loops += [
            value
            for cog in bot.cogs.values()
            for value in vars(cog).values()
            if isinstance(value, tasks.Loop)
        ]
        for name in list(bot.cogs):
            await bot.remove_cog(name)
        for _ in range(3):
            await asyncio.sleep(0)


def _walk(
    node: dict[str, Any],
    qualified: str,
    typed: dict[str, str],
    found: dict[str, dict[str, str]],
) -> None:
    qualified = f"{qualified} {node['name']}".strip()
    names = node.get("name_localizations") or {}
    typed = {
        locale: f"{typed[locale]} {names.get(discord_locale, node['name'])}".strip()
        for locale, discord_locale in DISCORD_LOCALES.items()
    }
    found[qualified] = {locale: f"/{path}" for locale, path in typed.items()}
    for option in node.get("options") or []:
        if option.get("type") in NESTED:
            _walk(option, qualified, typed, found)

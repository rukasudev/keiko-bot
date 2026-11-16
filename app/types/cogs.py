"""The cog bases every command cog is built on: each records its app commands."""

from typing import Any

from discord import app_commands
from discord.ext import commands

from app.bot import DiscordBot


def app_commands_decorator(cls: type[Any]) -> None:
    """Make `cls` record its app commands on the bot once it is built."""
    original_init = cls.__init__

    def new_init(self: Any, bot: DiscordBot, *args: Any, **kwargs: Any) -> None:
        original_init(self, bot, *args, **kwargs)
        commands = (
            self.commands if hasattr(self, "commands") else self.get_app_commands()
        )
        if not hasattr(self.bot, "app_commands"):
            self.bot.app_commands = []

        self.bot.app_commands.extend([command for command in commands])

    cls.__init__ = new_init


class GroupCog(commands.GroupCog):
    """A cog that is a slash command group of its own."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        app_commands_decorator(cls)


class Group(app_commands.Group):
    """A slash subgroup, attached under its command's parent group."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        app_commands_decorator(cls)


class Cog(commands.Cog):
    """A cog of slash commands, context menus or listeners."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        app_commands_decorator(cls)

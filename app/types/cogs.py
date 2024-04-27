from discord import app_commands
from discord.ext import commands

from app.bot import DiscordBot


def app_commands_decorator(cls):
    original_init = cls.__init__

    def new_init(self, bot: DiscordBot, *args, **kwargs):
        original_init(self, bot, *args, **kwargs)
        commands = (
            self.commands if hasattr(self, "commands") else self.get_app_commands()
        )
        if not hasattr(self.bot, "app_commands"):
            self.bot.app_commands = []

        self.bot.app_commands.extend([command for command in commands])

    cls.__init__ = new_init


class GroupCog(commands.GroupCog):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        app_commands_decorator(cls)


class Group(app_commands.Group):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        app_commands_decorator(cls)


class Cog(commands.Cog):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        app_commands_decorator(cls)

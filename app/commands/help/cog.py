"""/help: the list of Keiko's commands."""

import discord

from app.bot import DiscordBot
from app.commands.help.service import send_help
from app.decorators import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog


class Help(Cog, name=locale_str("help", type="name", namespace="help.command.slash")):
    """The /help slash command."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("help", type="name", namespace="help.command.slash"),
        description=locale_str(
            "help", type="description", namespace="help.command.slash"
        ),
    )
    async def help(self, interaction: discord.Interaction[DiscordBot]) -> None:
        """Open the command list."""
        await send_help(interaction)


async def setup(bot: DiscordBot) -> None:
    """Load the /help cog."""
    await bot.add_cog(Help(bot))

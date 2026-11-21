"""The /setup command: the dashboard of every feature."""

import discord

from app.bot import DiscordBot
from app.commands.setup.service import open_setup_dashboard
from app.core import analytics
from app.decorators import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog


class Setup(
    Cog, name=locale_str("setup", type="name", namespace="setup.command.slash")
):
    """The cog of /setup."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("setup", type="name", namespace="setup.command.slash"),
        description=locale_str(
            "setup", type="description", namespace="setup.command.slash"
        ),
    )
    @discord.app_commands.default_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        """Open the setup dashboard."""
        await open_setup_dashboard(interaction, analytics.resolve_source(interaction))


async def setup(bot: DiscordBot) -> None:
    """Add the /setup cog."""
    await bot.add_cog(Setup(bot))

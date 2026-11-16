"""/ping: Keiko's gateway latency."""

import discord

from app.bot import DiscordBot
from app.commands.help.service import ping_embed
from app.decorators import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog


class Ping(Cog, name="ping"):
    """The /ping slash command."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str(
            "ping", type="name", namespace="help.command.slash.commands.ping"
        ),
        description=locale_str(
            "ping", type="description", namespace="help.command.slash.commands.ping"
        ),
    )
    async def ping(self, interaction: discord.Interaction) -> None:
        """Answer Pong with the latency."""
        embed = ping_embed(self.bot, interaction.locale)
        await interaction.response.send_message(embed=embed)


async def setup(bot: DiscordBot) -> None:
    """Load the /ping cog."""
    await bot.add_cog(Ping(bot))

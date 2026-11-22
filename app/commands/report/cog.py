"""/report: a bug report sent to the team, to Notion and back to its author."""

import discord

from app.bot import DiscordBot
from app.commands.report.service import open_report
from app.decorators import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog


class Report(Cog, name="report"):
    """The /report slash command."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("report", type="name", namespace="report.command.slash"),
        description=locale_str(
            "report", type="description", namespace="report.command.slash"
        ),
    )
    async def report(self, interaction: discord.Interaction[DiscordBot]) -> None:
        """Open the modal a member writes the report in."""
        await open_report(interaction)


async def setup(bot: DiscordBot) -> None:
    """Load the /report cog."""
    await bot.add_cog(Report(bot))

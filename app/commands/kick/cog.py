"""The /notifications kick lives command and the daily resync of its subscriptions."""

import discord
from discord.ext import tasks

from app.bot import DiscordBot
from app.commands.kick.service import resync_subscriptions
from app.constants import Commands as constants
from app.core.trace import run_traced
from app.decorators import keiko_admin_only, keiko_command
from app.forms import open_feature
from app.translator import locale_str
from app.types.cogs import Cog, Group


class Kick(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str(
        "kick", type="subgroup", namespace="notifications_kick.command.slash"
    ),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str(
            "lives", type="name", namespace="notifications_kick.command.slash"
        ),
        description=locale_str(
            "desc", type="description", namespace="notifications_kick.command.slash"
        ),
    )
    @keiko_admin_only
    async def notifications_kick(self, interaction: discord.Interaction) -> None:
        """Open the setup form, or the manager of the Kick announcements."""
        await open_feature(interaction, "notifications_kick")


class KickResyncLoop(Cog):
    """Leaves Kick one live subscription per followed streamer, at start and then
    once a day."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    async def cog_load(self) -> None:
        """Start the resync loop."""
        self.resync_kick.start()

    async def cog_unload(self) -> None:
        """Stop the resync loop."""
        self.resync_kick.cancel()

    @tasks.loop(hours=constants.KICK_RESYNC_HOURS)
    async def resync_kick(self) -> None:
        """Resync every followed streamer's subscription, traced as one job."""
        await run_traced(resync_subscriptions(), "kick resync", source="job")

    @resync_kick.before_loop
    async def before_resync_kick(self) -> None:
        """Wait for the bot to be ready."""
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    """Add the resync cog."""
    await bot.add_cog(KickResyncLoop(bot))

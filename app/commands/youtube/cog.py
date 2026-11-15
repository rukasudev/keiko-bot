"""The /notifications youtube video command and the YouTube renewals at start."""

import discord
from discord.ext import tasks

from app.bot import DiscordBot
from app.commands.youtube import service as notifications_youtube_video
from app.core.trace import run_traced
from app.decorators import keiko_admin_only, keiko_command
from app.translator import locale_str
from app.types.cogs import Cog, Group


class Youtube(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str(
        "youtube", type="subgroup", namespace="notifications_youtube_video.slash"
    ),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str(
            "video", type="name", namespace="notifications_youtube_video.slash"
        ),
        description=locale_str(
            "desc", type="description", namespace="notifications_youtube_video.slash"
        ),
    )
    @keiko_admin_only
    async def notifications_youtube_video(
        self, interaction: discord.Interaction
    ) -> None:
        """Open the setup form, or the manager of the YouTube announcements."""
        await notifications_youtube_video.manager(interaction)


class YoutubeRenewals(Cog):
    """Subscribes every followed channel again with the hub secret, once per start."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    async def cog_load(self) -> None:
        """Start the one renewal pass."""
        self.resubscribe_youtube.start()

    async def cog_unload(self) -> None:
        """Stop the renewal pass."""
        self.resubscribe_youtube.cancel()

    @tasks.loop(count=1)
    async def resubscribe_youtube(self) -> None:
        """Subscribe every followed channel again, traced as one job."""
        await run_traced(
            notifications_youtube_video.resubscribe_followed_channels(),
            "youtube resubscribe",
            source="job",
        )

    @resubscribe_youtube.before_loop
    async def before_resubscribe_youtube(self) -> None:
        """Wait for the bot to be ready."""
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    """Add the renewals cog."""
    await bot.add_cog(YoutubeRenewals(bot))

from datetime import datetime, time

from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.services import admin_digest, analytics


class Analytics(commands.Cog):
    """Drains the analytics queue into storage on the bot's own loop."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.flush_events.start()
        self.send_weekly_digest.start()

    async def cog_unload(self) -> None:
        self.flush_events.cancel()
        self.send_weekly_digest.cancel()
        analytics.flush()

    @tasks.loop(seconds=constants.ANALYTICS_FLUSH_SECONDS)
    async def flush_events(self) -> None:
        try:
            analytics.flush()
        except Exception as error:
            logger.warn(
                f"Analytics flush failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @flush_events.before_loop
    async def before_flush(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=time(hour=constants.ANALYTICS_DIGEST_HOUR))
    async def send_weekly_digest(self) -> None:
        """Fires daily, acts on Mondays — the loop has no weekly cadence."""
        if datetime.now().weekday() != constants.ANALYTICS_DIGEST_WEEKDAY:
            return

        channel = self.bot.get_channel(self.bot.config.ADMIN_LOGS_CHANNEL_ID)
        if not channel:
            return

        try:
            await channel.send(embed=admin_digest.build_weekly_digest())
        except Exception as error:
            logger.warn(
                f"Weekly digest failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @send_weekly_digest.before_loop
    async def before_digest(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Analytics(bot))

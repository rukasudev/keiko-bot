from datetime import datetime, time

from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.services import admin_digest, analytics, debug_logs, logs_archive


class Analytics(commands.Cog):
    """Drains the buffered writes into storage on the bot's own loop.

    Both queues ride the same loop on purpose: they have the same shape — a
    bounded buffer that must never make a command wait on a database — and a
    second loop would be a second thing to reason about at shutdown.
    """

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.flush_events.start()
        self.send_weekly_digest.start()
        self.export_daily_logs.start()

    async def cog_unload(self) -> None:
        self.flush_events.cancel()
        self.send_weekly_digest.cancel()
        self.export_daily_logs.cancel()
        analytics.flush()
        debug_logs.stop_writer()
        debug_logs.flush()

    @tasks.loop(seconds=constants.ANALYTICS_FLUSH_SECONDS)
    async def flush_events(self) -> None:
        try:
            analytics.flush()
        except Exception as error:
            logger.warn(
                f"Analytics flush failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

        debug_logs.flush()

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
            # The bot is the only one that knows how many guilds it is in:
            # analytics only ever sees the guilds that did something.
            await channel.send(
                embed=admin_digest.build_weekly_digest(guild_count=len(self.bot.guilds))
            )
        except Exception as error:
            logger.warn(
                f"Weekly digest failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @send_weekly_digest.before_loop
    async def before_digest(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(
        time=time(
            hour=constants.DEBUG_LOGS_EXPORT_HOUR,
            minute=constants.DEBUG_LOGS_EXPORT_MINUTE,
        )
    )
    async def export_daily_logs(self) -> None:
        """Posts yesterday's logs to the same channel that keeps the text file.

        Independent of the rotating file handler by design: if Mongo is down the
        text file still ships, and if a deploy took the text file with it this
        export is still complete.
        """
        channel = self.bot.get_channel(self.bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)
        if not channel:
            return

        day = logs_archive.previous_day()

        try:
            daily_file, written = logs_archive.build_daily_file(day)
            if not daily_file:
                return
            await channel.send(logs_archive.build_message(day, written), file=daily_file)
        except Exception as error:
            logger.warn(
                f"Daily log export failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @export_daily_logs.before_loop
    async def before_export(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Analytics(bot))

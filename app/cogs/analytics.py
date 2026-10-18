import asyncio
from datetime import datetime, time, timezone

from discord.ext import commands, tasks

from app import lifecycle, logger
from app.bot import DiscordBot
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.services import admin_digest, analytics, analytics_sink, debug_logs, logs_archive


class Analytics(commands.Cog):
    """Keiko's own scheduled work, timed by the bot's loop and done in a thread: it drains the
    buffered writes into storage, sends the weekly digest, and keeps the logs files channel
    (the daily logs, each month's events, the daily backup and the old files' cleanup).

    Both queues ride the same loop on purpose: they have the same shape — a
    bounded buffer that must never make a command wait on a database — and a
    second loop would be a second thing to reason about at shutdown.
    """

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.flush_events.start()
        self.send_weekly_digest.start()
        self.export_daily_logs.start()
        self.archive_months.start()
        self.measure_guild_sizes.start()
        self.post_daily_backup.start()

    async def cog_unload(self) -> None:
        self.flush_events.cancel()
        self.send_weekly_digest.cancel()
        self.export_daily_logs.cancel()
        self.archive_months.cancel()
        self.measure_guild_sizes.cancel()
        self.post_daily_backup.cancel()
        await asyncio.to_thread(lifecycle.write_queues)

    @tasks.loop(seconds=constants.ANALYTICS_FLUSH_SECONDS)
    async def flush_events(self) -> None:
        try:
            await asyncio.to_thread(analytics.flush)
        except Exception as error:
            logger.warn(
                f"Analytics flush failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

        await asyncio.to_thread(debug_logs.flush)

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
            digest = await asyncio.to_thread(
                admin_digest.build_weekly_digest, guild_count=len(self.bot.guilds)
            )
            await channel.send(embed=digest)
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
        channel = logs_archive.files_channel(self.bot, "Daily log export")
        if channel is None:
            return

        day = logs_archive.previous_day()

        try:
            daily_file, written = await asyncio.to_thread(logs_archive.build_daily_file, day)
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


    @tasks.loop(
        time=time(
            hour=constants.ANALYTICS_ARCHIVE_HOUR,
            minute=constants.ANALYTICS_ARCHIVE_MINUTE,
        )
    )
    async def archive_months(self) -> None:
        """Posts, oldest first, every complete month the raw events still reach and no pass
        has posted, so a month whose passes all failed is caught up while it lasts."""
        now = datetime.now(timezone.utc)

        try:
            months = await asyncio.to_thread(logs_archive.months_to_archive, now)
        except Exception as error:
            logger.error(
                f"Monthly events archive failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                exc_info=True,
            )
            return

        if not months:
            return

        channel = logs_archive.files_channel(self.bot, "Monthly events archive")
        if channel is None:
            return

        recipient = logs_archive.sealing_key(self.bot, "Monthly events archive")
        if recipient is None:
            return

        for month in months:
            try:
                files = await asyncio.to_thread(
                    logs_archive.build_month_files,
                    month,
                    channel.guild.filesize_limit,
                    recipient,
                )
                if files:
                    await logs_archive.send_files(
                        channel,
                        logs_archive.build_month_message(month, files),
                        [(part.filename, part.payload) for part in files],
                    )
                await asyncio.to_thread(logs_archive.mark_archived, month, files, now)
            except Exception as error:
                logger.error(
                    f"Monthly events archive failed for {month:%Y-%m}: "
                    f"{type(error).__name__}: {error}",
                    log_type=logconstants.COMMAND_ERROR_TYPE,
                    exc_info=True,
                )

    @archive_months.before_loop
    async def before_archive(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(
        time=time(
            hour=constants.ANALYTICS_ARCHIVE_HOUR,
            minute=constants.ANALYTICS_ARCHIVE_MINUTE,
        )
    )
    async def measure_guild_sizes(self) -> None:
        """Writes every guild's current size bucket on its profile."""
        member_counts = {str(guild.id): guild.member_count for guild in self.bot.guilds}

        try:
            await asyncio.to_thread(
                analytics_sink.record_guild_sizes, member_counts, datetime.now(timezone.utc)
            )
        except Exception as error:
            logger.warn(
                f"Guild sizes not recorded: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @measure_guild_sizes.before_loop
    async def before_measure(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=time(hour=constants.BACKUP_HOUR, minute=constants.BACKUP_MINUTE))
    async def post_daily_backup(self) -> None:
        """Builds the backup off the loop, sealed, and posts it to the logs files channel,
        then deletes Keiko's old files there: the backups only on a day one went out."""
        channel = logs_archive.files_channel(self.bot, "Daily backup")
        if channel is None:
            return

        now = datetime.now(timezone.utc)
        posted = False

        recipient = logs_archive.sealing_key(self.bot, "Daily backup")
        if recipient is not None:
            try:
                limit = channel.guild.filesize_limit
                files, skipped = await asyncio.to_thread(
                    logs_archive.build_backup, now, limit, recipient
                )
                if skipped:
                    logger.warn(
                        f"Daily backup left out {len(skipped)} collection(s), each over "
                        f"the channel's {limit} bytes on its own: {', '.join(skipped)}",
                        log_type=logconstants.COMMAND_WARN_TYPE,
                    )

                await logs_archive.send_files(
                    channel,
                    logs_archive.build_summary(now, files, skipped),
                    [(part.filename, part.payload) for part in files],
                )
                posted = bool(files)
            except Exception as error:
                logger.error(
                    f"Daily backup failed: {type(error).__name__}: {error}",
                    log_type=logconstants.COMMAND_ERROR_TYPE,
                    exc_info=True,
                )

        cleared = await logs_archive.delete_old_files(
            channel, self.bot.user.id, now, with_backups=posted
        )
        if cleared.stopped or cleared.failures:
            logger.warn(cleared.report(), log_type=logconstants.COMMAND_WARN_TYPE)
        elif cleared.left:
            logger.info(cleared.report(), log_type=logconstants.COMMAND_INFO_TYPE)

    @post_daily_backup.before_loop
    async def before_backup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Analytics(bot))

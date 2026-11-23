import asyncio
from datetime import datetime, time, timezone
from typing import Dict, List

from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot, flush_queues
from app.constants import Commands as constants
from app.constants import Dependencies
from app.constants import LogTypes as logconstants
from app.core import (
    analytics,
    analytics_writer,
    debug_logs,
    dispatch,
    logs_archive,
    trace,
)
from app.core.threads import to_thread
from app.data import data


Running = Dict[str, "asyncio.Task[None]"]


async def start_due_schedules(
    now: datetime, running: Running
) -> List["asyncio.Task[None]"]:
    """Start the batch of schedules due by `now` of every kind a command runs, each
    kind a task of its own kept in `running` until it ends, and leave out a kind whose
    last batch still runs, so one slow or failing kind never holds back the next."""
    kinds = tuple(kind for kind in dispatch.schedule_kinds() if kind not in running)
    if not kinds:
        return []

    try:
        due = await data().keiko_schedules.find_due_schedules(
            kinds, now, constants.SCHEDULES_DUE_LIMIT
        )
    except Exception as error:
        logger.warn(
            f"Due schedules not read: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return []

    started = []
    for kind in kinds:
        batch = [schedule for schedule in due if schedule.get("kind") == kind]
        if not batch:
            continue
        handed = dispatch.schedules(kind, batch)
        task = asyncio.create_task(
            trace.run_traced(handed, f"due {kind}", source="job")
        )
        running[kind] = task
        task.add_done_callback(lambda _done, kind=kind: running.pop(kind, None))
        started.append(task)
    return started


async def hand_due_schedules(now: datetime) -> None:
    """Hand the schedules due by `now` to the commands that own their kinds, and wait
    until every kind's batch ended."""
    await asyncio.gather(*await start_due_schedules(now, {}))


class Analytics(commands.Cog):
    """Keiko's own scheduled work, timed by the bot's loop: it drains the buffered writes into
    storage and keeps the logs files channel (the daily logs, each
    month's events, the daily backup and the old files' cleanup), its files built in a thread.

    Both queues ride the same loop on purpose: they have the same shape — a
    bounded buffer that must never make a command wait on a database — and a
    second loop would be a second thing to reason about at shutdown.
    """

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        trace.register_sink(trace.keep_run)
        self.flush_events.start()
        self.export_daily_logs.start()
        self.archive_months.start()
        self.measure_guild_sizes.start()
        self.post_daily_backup.start()

    async def cog_unload(self) -> None:
        await self._finish_flush()
        self.export_daily_logs.cancel()
        self.archive_months.cancel()
        self.measure_guild_sizes.cancel()
        self.post_daily_backup.cancel()
        await flush_queues()

    async def _finish_flush(self) -> None:
        """Let a flush under way end its batch, for a bounded time, before the last one."""
        running = self.flush_events.get_task()
        self.flush_events.stop()
        if running is not None and not running.done():
            await asyncio.wait({running}, timeout=Dependencies.BLOCKING_IO_STOP_WAIT_SECONDS)
        self.flush_events.cancel()

    @tasks.loop(seconds=constants.ANALYTICS_FLUSH_SECONDS)
    async def flush_events(self) -> None:
        try:
            await analytics.flush()
        except Exception as error:
            logger.warn(
                f"Analytics flush failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

        await debug_logs.flush()

    @flush_events.before_loop
    async def before_flush(self) -> None:
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
            daily_file, written = await to_thread(logs_archive.build_daily_file, day)
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
            months = await logs_archive.months_to_archive(now)
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
                files = await to_thread(
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
                await logs_archive.mark_archived(month, files, now)
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
            await analytics_writer.record_guild_sizes(member_counts, datetime.now(timezone.utc))
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
                files, skipped = await to_thread(
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


class DueSchedules(commands.Cog):
    """Every `SCHEDULES_CHECK_SECONDS`, hands the schedules due by then to the commands
    that own their kinds, each kind's batch running on its own."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.running: Running = {}

    async def cog_load(self) -> None:
        """Start the loop."""
        self.run_due_schedules.start()

    async def cog_unload(self) -> None:
        """Stop the loop and the batches still running."""
        self.run_due_schedules.cancel()
        for task in list(self.running.values()):
            task.cancel()

    @tasks.loop(seconds=constants.SCHEDULES_CHECK_SECONDS)
    async def run_due_schedules(self) -> None:
        """Start the schedules due by now, leaving out a kind still running."""
        await start_due_schedules(datetime.now(timezone.utc), self.running)

    @run_due_schedules.before_loop
    async def before_due_schedules(self) -> None:
        """Wait for the bot to be ready."""
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Analytics(bot))
    await bot.add_cog(DueSchedules(bot))

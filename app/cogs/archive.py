import asyncio
from datetime import datetime, time, timezone

from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.services import archive, logs_archive, sealing


class Archive(commands.Cog):
    """Keeps what analytics would lose: each month's raw events, every guild's current size."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.archive_months.start()
        self.measure_guild_sizes.start()

    async def cog_unload(self) -> None:
        self.archive_months.cancel()
        self.measure_guild_sizes.cancel()

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
            months = await asyncio.to_thread(archive.months_to_archive, now)
        except Exception as error:
            logger.error(
                f"Monthly events archive failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                exc_info=True,
            )
            return

        if not months:
            return

        channel = self.bot.get_channel(self.bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)
        if not channel:
            logger.warn(
                "Monthly events archive not posted: the logs files channel was not found",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return

        recipient = sealing.age_recipient(self.bot.config.BACKUP_AGE_PUBLIC_KEY)
        if recipient is None:
            logger.warn(
                "Monthly events archive not posted: /keiko/backup/age_public_key "
                "(BACKUP_AGE_PUBLIC_KEY locally) is missing or is not an age public key, "
                "and the events are never posted unencrypted",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return

        for month in months:
            try:
                files = await asyncio.to_thread(
                    archive.build_month_files, month, channel.guild.filesize_limit, recipient
                )
                if files:
                    await logs_archive.send_files(
                        channel,
                        archive.build_month_message(month, files),
                        [(part.filename, part.payload) for part in files],
                    )
                await asyncio.to_thread(archive.mark_archived, month, files, now)
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
                archive.record_guild_sizes, member_counts, datetime.now(timezone.utc)
            )
        except Exception as error:
            logger.warn(
                f"Guild sizes not recorded: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @measure_guild_sizes.before_loop
    async def before_measure(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Archive(bot))

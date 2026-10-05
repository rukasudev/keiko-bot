import asyncio
from datetime import datetime, time, timezone

from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.services import backup, logs_archive, logs_files, sealing


class Backup(commands.Cog):
    """Posts a sealed copy of Keiko's database once a day where the daily logs go, and
    deletes Keiko's own files there once past their kind's age."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.post_daily_backup.start()

    async def cog_unload(self) -> None:
        self.post_daily_backup.cancel()

    @tasks.loop(time=time(hour=constants.BACKUP_HOUR, minute=constants.BACKUP_MINUTE))
    async def post_daily_backup(self) -> None:
        """Builds the backup off the loop, sealed, and posts it to the logs files channel,
        then deletes Keiko's old files there: the backups only on a day one went out."""
        channel = self.bot.get_channel(self.bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)
        if not channel:
            logger.warn(
                "Daily backup not posted: the logs files channel was not found",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return

        now = datetime.now(timezone.utc)
        posted = False

        recipient = sealing.age_recipient(self.bot.config.BACKUP_AGE_PUBLIC_KEY)
        if recipient is None:
            logger.warn(
                "Daily backup not posted: /keiko/backup/age_public_key "
                "(BACKUP_AGE_PUBLIC_KEY locally) is missing or is not an age public key, "
                "and the database is never posted unencrypted",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
        else:
            try:
                limit = channel.guild.filesize_limit
                files, skipped = await asyncio.to_thread(
                    backup.build_backup, now, limit, recipient
                )
                if skipped:
                    logger.warn(
                        f"Daily backup left out {len(skipped)} collection(s), each over "
                        f"the channel's {limit} bytes on its own: {', '.join(skipped)}",
                        log_type=logconstants.COMMAND_WARN_TYPE,
                    )

                await logs_archive.send_files(
                    channel,
                    backup.build_summary(now, files, skipped),
                    [(part.filename, part.payload) for part in files],
                )
                posted = bool(files)
            except Exception as error:
                logger.error(
                    f"Daily backup failed: {type(error).__name__}: {error}",
                    log_type=logconstants.COMMAND_ERROR_TYPE,
                    exc_info=True,
                )

        cleared = await logs_files.delete_old_files(
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
    await bot.add_cog(Backup(bot))

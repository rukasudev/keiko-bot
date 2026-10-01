import asyncio
from datetime import datetime, time, timezone

from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.services import backup


class Backup(commands.Cog):
    """Posts a copy of Keiko's database once a day, where the daily logs already go."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.post_daily_backup.start()

    async def cog_unload(self) -> None:
        self.post_daily_backup.cancel()

    @tasks.loop(time=time(hour=constants.BACKUP_HOUR, minute=constants.BACKUP_MINUTE))
    async def post_daily_backup(self) -> None:
        """Builds the backup off the loop and posts it to the logs files channel."""
        channel = self.bot.get_channel(self.bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)
        if not channel:
            logger.warn(
                "Daily backup not posted: the logs files channel was not found",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return

        now = datetime.now(timezone.utc)
        try:
            limit = channel.guild.filesize_limit
            files, skipped = await asyncio.to_thread(backup.build_backup, now, limit)
            if skipped:
                logger.warn(
                    f"Daily backup left out {len(skipped)} collection(s), each over "
                    f"the channel's {limit} bytes on its own: {', '.join(skipped)}",
                    log_type=logconstants.COMMAND_WARN_TYPE,
                )

            first = backup.build_file(files[0]) if files else None
            await channel.send(backup.build_summary(now, files, skipped), file=first)
            for part in files[1:]:
                await channel.send(file=backup.build_file(part))
        except Exception as error:
            logger.error(
                f"Daily backup failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                exc_info=True,
            )

    @post_daily_backup.before_loop
    async def before_backup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Backup(bot))

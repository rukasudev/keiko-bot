import asyncio

from discord import app_commands
from discord.ext import tasks

from app.bot import DiscordBot
from app.cogs.notifications.twitch import Twitch
from app.cogs.notifications.youtube import Youtube
from app.services import notifications_youtube_video
from app.services.trace import run_traced
from app.translator import locale_str
from app.types.cogs import GroupCog


@app_commands.guild_only()
class Notifications(GroupCog, name=locale_str("notifications", type="groups")):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    async def cog_load(self) -> None:
        self.resubscribe_youtube.start()

    async def cog_unload(self) -> None:
        self.resubscribe_youtube.cancel()

    @tasks.loop(count=1)
    async def resubscribe_youtube(self) -> None:
        """Subscribe every followed YouTube channel again with the hub secret, once per start."""
        await run_traced(
            asyncio.to_thread(notifications_youtube_video.resubscribe_followed_channels),
            "youtube resubscribe",
            source="job",
        )

    @resubscribe_youtube.before_loop
    async def before_resubscribe_youtube(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    notifications = Notifications(bot)

    notifications.app_command.add_command(Twitch(bot))
    notifications.app_command.add_command(Youtube(bot))

    await bot.add_cog(notifications)

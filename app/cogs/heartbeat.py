import asyncio
from datetime import timedelta

import discord
import requests
from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.services import metrics
from app.services.trace import trace_scope


class Heartbeat(commands.Cog):
    """Tells the monitor Keiko is alive and connected, and Prometheus how late it runs.

    A monitor that stops hearing the ping is how a stopped, frozen or disconnected
    bot gets noticed; the lag histogram is how a slow one does.
    """

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.connected = True

    async def cog_load(self) -> None:
        metrics.record_build_info(self.bot.config.APP_VERSION)
        self.beat.start()

    async def cog_unload(self) -> None:
        self.beat.cancel()

    @commands.Cog.listener("on_ready")
    @commands.Cog.listener("on_resumed")
    async def on_connected(self) -> None:
        """The gateway is back, so a ping tells the truth again."""
        self.connected = True

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        """The gateway dropped, so the monitor must stop hearing that all is well."""
        self.connected = False

    @tasks.loop(seconds=constants.HEARTBEAT_SECONDS)
    async def beat(self) -> None:
        """Record how late this tick woke up against its schedule, then ping the monitor."""
        due = self.beat.next_iteration
        if due is not None:
            scheduled = due - timedelta(seconds=self.beat.seconds)
            metrics.record_loop_lag((discord.utils.utcnow() - scheduled).total_seconds())

        url = self.bot.config.HEARTBEAT_URL
        if not url or not self.connected:
            return

        async with trace_scope("heartbeat", source="internal", silent_when_clean=True):
            try:
                await asyncio.to_thread(_ping, url)
            except Exception as error:
                logger.warn(
                    f"Heartbeat ping failed: {type(error).__name__}",
                    log_type=logconstants.COMMAND_WARN_TYPE,
                )

    @beat.before_loop
    async def before_beat(self) -> None:
        await self.bot.wait_until_ready()


def _ping(url: str) -> None:
    requests.get(url, timeout=constants.HEARTBEAT_TIMEOUT_SECONDS).raise_for_status()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Heartbeat(bot))

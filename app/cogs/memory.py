import asyncio

from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import MemoryMonitor as constants
from app.services import memory_monitor


class Memory(commands.Cog):
    """Publishes what holds the bot's memory, so it can be watched over days."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.samples = 0
        memory_monitor.start_tracing()
        self.watch.start()

    async def cog_unload(self) -> None:
        self.watch.cancel()

    @tasks.loop(minutes=constants.SAMPLE_MINUTES)
    async def watch(self) -> None:
        try:
            memory_monitor.sample(self.bot)
            if self.samples % constants.CENSUS_EVERY_SAMPLES == 0:
                await asyncio.to_thread(memory_monitor.census)
            if self.samples % constants.TRACE_EVERY_SAMPLES == 0:
                await asyncio.to_thread(memory_monitor.trace)
        except Exception as error:
            logger.warn(f"Memory monitor could not read the process: {type(error).__name__}: {error}")
        self.samples += 1

    @watch.before_loop
    async def before_watch(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Memory(bot))

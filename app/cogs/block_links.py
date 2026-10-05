import asyncio

import discord
from discord.ext import commands

from app import logger
from app.bot import DiscordBot
from app.constants import LogTypes as logconstants
from app.services import block_links


class BlockLinks(commands.Cog):
    """What block links does when Keiko leaves a server: its own counts go with it."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Deletes the server's block counts; Keiko's own count keeps what they added to it."""
        try:
            await asyncio.to_thread(block_links.forget_counters, str(guild.id))
        except Exception as error:
            logger.warn(
                "Block counts of a server Keiko left were not deleted: "
                f"{type(error).__name__}: {error}",
                guild_id=guild.id,
                log_type=logconstants.COMMAND_WARN_TYPE,
            )


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(BlockLinks(bot))

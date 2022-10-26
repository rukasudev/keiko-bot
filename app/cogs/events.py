import discord

from app.bot import DiscordBot
from app.logger import logger
from datetime import datetime
from discord.ext import commands


class Events(commands.Cog, name="Events"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()

        if not self.bot.synced:
            await self.bot.tree.sync()
            self.bot.synced = True

        self.bot.ready_time = datetime.utcnow()

        ready_message = (
            f"\n---------------------------------------------------\n"
            f"Bot Ready!\n"
            f"Current Prefix: {self.bot.config.PREFIX}\n"
            f"---------------------------------------------------"
        )
        logger.info(ready_message)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_id = str(member.guild.id)

        # send_welcome_message()
        # guild = member.guild
        # role_id = 838123185978998788
        # apply_role_in_member()
        pass


async def setup(bot):
    await bot.add_cog(Events(bot))

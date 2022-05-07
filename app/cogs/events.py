import discord
from logger import logger
from datetime import datetime
from discord.ext import commands
from cogs.moderations import Moderation


class Events(commands.Cog, name="Events"):
    def __init__(self, bot):
        self.bot = bot
        self.moderation = Moderation(bot)

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.ready_time = datetime.utcnow()

        ready_message = (
            f"\n---------------------------------------------------\n"
            f"Bot Ready!\n"
            f"Current Prefix: {self.bot.config.PREFIX}\n"
            f"---------------------------------------------------"
        )
        logger.info(ready_message)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild_id = str(member.guild.id)

        # send_welcome_message()
        # guild = member.guild
        # role_id = 838123185978998788
        # apply_role_in_member()
        pass

    @commands.Cog.listener()
    async def on_message(self, message):
        guild_id = str(message.guild.id)

        await self.moderation.block_links(message, guild_id)


def setup(bot):
    bot.add_cog(Events(bot))

from datetime import datetime

import discord
from discord.app_commands import locale_str
from discord.ext import commands

from app.bot import DiscordBot
from app.logger import logger


class Events(commands.Cog, name=locale_str("events", namespace="commands")):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    # TODO: Improve this message
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.bot.wait_until_ready()

        if not self.bot.synced:
            await self.bot.tree.sync()
            self.bot.synced = True

        self.bot.ready_time = datetime.utcnow()

        ready_message = (
            f"\n---------------------------------------------------\n"
            f"Bot Ready!\n"
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

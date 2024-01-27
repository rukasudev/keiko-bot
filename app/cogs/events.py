from datetime import datetime

import discord
from discord.app_commands import locale_str
from discord.ext import commands

from app.bot import DiscordBot
from app.logger import logger


class Events(commands.Cog, name=locale_str("events", namespace="commands")):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.bot.wait_until_ready()

        if not self.bot.synced:
            await self.bot.tree.sync()
            self.bot.synced = True

        self.bot.ready_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        ready_message = (
            f"\n---------------------------------------------------\n"
            f"🎉 Corgi Initialized Successfully!\n"
            f"⏰ Ready Time: {self.bot.ready_time}\n"
            f"🔁 Synced with Tree: {'Yes' if self.bot.synced else 'No'}\n"
            f"🤖 Bot Name: {self.bot.application.name}\n"
            f"👤 Author: {self.bot.application.owner.name}\n"
            f"🏠 Total Guilds: {len(self.bot.guilds)}\n"
            f"👥 Total Users: {len(self.bot.users)}\n"
            f"📌 Prefix: {self.bot.command_prefix}\n"
            f"🎮 Current Activity: {self.bot.activity.name}\n"
            f"🐶 Current Status: {self.bot.status.name}️\n"
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

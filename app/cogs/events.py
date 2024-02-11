from datetime import datetime

from discord.app_commands import locale_str
from discord.ext import commands

from app.bot import DiscordBot
from app.logger import logger
from app.data.moderations import insert_parameters_by_guild, find_parameters_by_guild


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
    async def on_guild_join(self, guild):
        exist = find_parameters_by_guild(guild.id)
        if not exist:
            insert_parameters_by_guild(guild.id)


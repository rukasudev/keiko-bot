from datetime import datetime

import discord
from discord.app_commands import locale_str
from discord.ext import commands

from app import logger
from app.bot import DiscordBot
from app.constants import CogsConstants as constants
from app.data.moderations import (
    find_moderations_by_guild,
    insert_moderations_by_guild,
    update_moderations_by_guild,
)


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
    async def on_guild_join(self, guild: discord.Guild):
        exist = find_moderations_by_guild(guild.id)
        if not exist:
            return insert_moderations_by_guild(guild.id)

        return update_moderations_by_guild(guild.id, constants.IS_BOT_ONLINE, True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        return update_moderations_by_guild(guild.id, constants.IS_BOT_ONLINE, False)

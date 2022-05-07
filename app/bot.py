from discord.ext import commands
from discord.ext.commands import Bot
import asyncio

import discord
import threading
import os


class DiscordBot(Bot):
    """
    Wraps some interactions with the discord bot API, handles running the
    CommandProcessor when commands are received from discord messages
    """

    def __init__(self, config, mongo_db):
        self.config = config
        self.intents_bot = discord.Intents.all()
        self.intents_bot.members = True
        self.mongo_db = mongo_db
        self.guild_available = threading.Event()
        super().__init__(
            command_prefix=self.config.PREFIX,
            case_insensitive=True,
            self_bot=False,
            help_command=None,
            owner_id=self.config.OWNER_ID,
            intents=self.intents_bot,
            status=discord.Status.idle,
            activity=discord.Activity(
                type=discord.ActivityType.watching, name=self.config.DESCRIPTION
            ),
        )

    async def run(self):
        await super().start(self.config.BOT_TOKEN)

    def stop(self):
        self.bot.logout()

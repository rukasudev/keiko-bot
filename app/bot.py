import threading

import discord
from discord.ext.commands import Bot

from app.translator import Translator


class DiscordBot(Bot):
    """
    Wraps some interactions with the discord bot API, handles running the
    CommandProcessor when commands are received from discord messages
    """

    def __init__(self, config) -> None:
        self.config = config
        self.intents_bot = discord.Intents.all()
        self.intents_bot.members = True
        self.guild_available = threading.Event()
        self.synced = False
        super().__init__(
            command_prefix=self.config.PREFIX,
            application_id=self.config.APPLICATION_ID,
            case_insensitive=True,
            self_bot=False,
            help_command=None,
            owner_id=self.config.OWNER_ID,
            intents=self.intents_bot,
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.playing, name=self.config.DESCRIPTION
            ),
        )

    async def setup_hook(self) -> None:
        await self.load_extension("app.cogs.__init__")        
        await self.tree.set_translator(Translator())        
        await self.tree.sync()

    async def run(self) -> None:
        async with self:            
            await self.start(self.config.BOT_TOKEN)

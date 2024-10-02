import threading

import discord
from discord.ext.commands import Bot

from app.config import AppConfig
from app.integrations.notion import NotionIntegration
from app.integrations.twitch import TwitchClient
from app.integrations.youtube import YoutubeClient
from app.services.utils import cogs_manager, get_cogs_folder
from app.translator import Translator


class DiscordBot(Bot):
    """
    Wraps some interactions with the discord bot API, handles running the
    CommandProcessor when commands are received from discord messages
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.guild_available = threading.Event()
        self.synced = False
        self.notion = NotionIntegration(config)
        self.twitch = TwitchClient(self)
        self.youtube = YoutubeClient(self)
        super().__init__(
            command_prefix=self.config.PREFIX,
            application_id=self.config.APPLICATION_ID,
            case_insensitive=True,
            help_command=None,
            owner_id=self.config.OWNER_ID,
            intents=discord.Intents.all(),
            status=self.config.STATUS,
            activity=discord.Activity(
                type=self.config.ACTIVITY, name=self.config.DESCRIPTION
            ),
        )

    async def setup_hook(self) -> None:
        await cogs_manager(self, "load", get_cogs_folder())
        await self.tree.set_translator(Translator(self))

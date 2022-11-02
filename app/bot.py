import discord
import threading

from discord.ext.commands import Bot


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
            help_command=False,
            owner_id=self.config.OWNER_ID,
            intents=self.intents_bot,
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.playing, name=self.config.DESCRIPTION
            ),
        )

    async def load_cogs(self) -> None:
        await self.load_extension("app.cogs.__init__")

    async def run(self) -> None:
        async with self:
            await self.load_cogs()
            await self.start(self.config.BOT_TOKEN)

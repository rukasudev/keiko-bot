import discord
import threading

from discord.ext.commands import Bot


class DiscordBot(Bot):
    """
    Wraps some interactions with the discord bot API, handles running the
    CommandProcessor when commands are received from discord messages
    """

    def __init__(self, config):
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
            status=discord.Status.idle,
            activity=discord.Activity(
                type=discord.ActivityType.playing, name=self.config.DESCRIPTION
            ),
        )

    async def load_cogs(self):
        cogs = ["events", "moderations", "twitch", "block_links"]

        for func in cogs:
            await self.load_extension(f"app.cogs.{func}")

    async def run(self):
        async with self:
            await self.load_cogs()
            await self.start(self.config.BOT_TOKEN)

    def stop(self):
        self.logout()

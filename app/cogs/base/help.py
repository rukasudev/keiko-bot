import discord

from app.bot import DiscordBot
from app.decorators import keiko_command
from app.services.help import send_help
from app.translator import locale_str
from app.types.cogs import Cog


class Help(Cog, name=locale_str("help", type="name", namespace="help")):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("help", type="name", namespace="help"),
        description=locale_str("help", type="desc", namespace="help"),
    )
    async def help(self, interaction: discord.Interaction) -> None:
        await send_help(interaction)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Help(bot))

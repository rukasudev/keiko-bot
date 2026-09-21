import discord

from app.bot import DiscordBot
from app.decorators import keiko_command
from app.services import analytics
from app.translator import locale_str
from app.types.cogs import Cog
from app.services.setup import open_setup_dashboard


class Setup(Cog, name=locale_str("setup", type="name", namespace="setup")):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("setup", type="name", namespace="setup"),
        description=locale_str("setup", type="desc", namespace="setup"),
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        await open_setup_dashboard(interaction, analytics.resolve_source(interaction))


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Setup(bot))

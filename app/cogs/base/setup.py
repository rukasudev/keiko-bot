import discord

from app.bot import DiscordBot
from app.decorators import keiko_command
from app.services import analytics
from app.services.utils import parse_locale
from app.translator import locale_str
from app.types.cogs import Cog
from app.services.setup import setup_dashboard


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
        analytics.emit(
            "dashboard.opened",
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            source=analytics.resolve_source(interaction),
        )
        view = await setup_dashboard(
            str(interaction.guild.id), parse_locale(interaction.locale)
        )
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Setup(bot))

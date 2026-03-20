import discord

from app.bot import DiscordBot
from app.data.moderations import find_moderations_by_guild
from app.services.utils import keiko_command, parse_locale
from app.translator import locale_str
from app.types.cogs import Cog
from app.views.setup import SetupView


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
        locale = parse_locale(interaction.locale)
        moderations = find_moderations_by_guild(interaction.guild.id) or {}
        view = SetupView(moderations, locale)
        embed = view.get_embed()

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Setup(bot))

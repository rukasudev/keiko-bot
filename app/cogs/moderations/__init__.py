import discord
from discord import app_commands
from discord.app_commands import locale_str
from app.services.utils import parse_locale
from discord.ext import commands
from i18n import t

from app.bot import DiscordBot


@app_commands.guild_only()
class Moderations(
    commands.GroupCog, name=locale_str("moderations", namespace="commands")
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @app_commands.command(
        name=locale_str("sync", namespace="commands"),
        description=locale_str("syncdesc", namespace="commands"),
    )
    async def _sync(self, interaction: discord.Interaction):
        await self.bot.tree.sync(guild=discord.Object(id=interaction.guild.id))
        message = t("commands.syncreply", locale=parse_locale(interaction.locale))

        await interaction.response.send_message(message)

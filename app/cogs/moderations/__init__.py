import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

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

        await interaction.response.send_message("Sincronizado!")

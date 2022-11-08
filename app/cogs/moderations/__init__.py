import discord
from discord import app_commands
from discord.ext import commands

from app.bot import DiscordBot
from app.cogs.moderations.block import Block
from app.logger import logger


@app_commands.guild_only()
class Moderations(commands.GroupCog, name="moderações"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @app_commands.command(
        name="sync",
        description="Sincronizar slash commands com o servidor",
    )
    async def _sync(self, interaction: discord.Interaction):
        await self.bot.tree.sync(guild=discord.Object(id=interaction.guild.id))

        await interaction.response.send_message("Sincronizado!")


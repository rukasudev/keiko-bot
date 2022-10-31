import discord

from app.bot import DiscordBot
from app.logger import logger
from discord import app_commands
from discord.ext import commands


class Moderations(commands.Cog, name="Moderations"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot

    @app_commands.command(
        name="sync",
        description="Sincronizar slash commands com o servidor",
    )
    async def _sync(self, interaction: discord.Interaction):
        await self.bot.tree.sync(guild=discord.Object(id=interaction.guild.id))

        await interaction.response.send_message("Sincronizado!")


async def setup(bot):
    await bot.add_cog(Moderations(bot))

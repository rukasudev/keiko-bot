import discord

from app.bot import DiscordBot
from app.logger import logger
from discord import app_commands
from discord.ext import commands


@app_commands.guild_only()
class Notifications(commands.GroupCog, name="notificações"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @app_commands.command(
        name="hello",
        description="Sincronizar slash commands com o servidor",
    )
    async def _sync(self, interaction: discord.Interaction):
        await interaction.response.send_message("Hello!")

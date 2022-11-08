import discord
from discord import app_commands


class Twitch(app_commands.Group, name="twitch"):
    @app_commands.command(
        name="on",
        description="Sincronizar slash commands com o servidor",
    )
    async def _sync(self, interaction: discord.Interaction):
        await interaction.response.send_message("Hello!")

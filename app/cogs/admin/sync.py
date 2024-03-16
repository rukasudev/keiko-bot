import discord
from discord import app_commands

from app.bot import DiscordBot


class Sync(app_commands.Group, name="sync"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @app_commands.command(
        name="guild",
        description="Keiko has synchronized the command tree specifically for this guild",
    )
    async def sync_guild(self, interaction: discord.Interaction):
        await self.bot.tree.sync(guild=interaction.guild)
        message = "Everything is now up-to-date and ready to use!"

        await interaction.response.send_message(message)

    @app_commands.command(
        name="global",
        description="Keiko has synchronized the global command tree for all guilds",
    )
    async def sync_global(self, interaction: discord.Interaction):
        await self.bot.tree.sync()
        message = "Now, every corner of Keiko's world is in harmony and ready for your commands!"

        await interaction.response.send_message(message)

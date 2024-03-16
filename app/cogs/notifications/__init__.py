import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from app.bot import DiscordBot
from app.cogs.notifications.twitch import Twitch


@app_commands.guild_only()
class Notifications(
    commands.GroupCog, name=locale_str("notifications", namespace="commands")
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @app_commands.command(
        name="hello",
        description="Sincronizar slash commands com o servidor",
    )
    async def _sync(self, interaction: discord.Interaction):
        await self.bot.tree.sync(guild=interaction.guild)
        await interaction.response.send_message("Hello!")


async def setup(bot: DiscordBot) -> None:
    notifications = Notifications(bot)
    notifications.app_command.add_command(Twitch())

    await bot.add_cog(notifications)

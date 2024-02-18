from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from app.bot import DiscordBot


@app_commands.guild_only()
@app_commands.default_permissions()
class Moderations(
    commands.GroupCog, name=locale_str("moderations", namespace="commands")
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

from discord import app_commands
import discord
from discord.app_commands import locale_str
from app.services.utils import parse_locale
from discord.ext import commands
from i18n import t

from app.bot import DiscordBot

@app_commands.guild_only()
@commands.is_owner()
class Admin(
    commands.GroupCog, name=locale_str("admin", namespace="commands")
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

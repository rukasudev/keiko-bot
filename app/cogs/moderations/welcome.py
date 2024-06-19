import discord

from app.bot import DiscordBot
from app.services import welcome_messages as welcome_messages_service
from app.services.utils import keiko_command
from app.translator import locale_str
from app.types.cogs import Group


class Welcome(
    Group,
    name=locale_str("welcome", type="subgroup", namespace="welcome-messages"),
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("messages", type="name", namespace="welcome-messages"),
        description=locale_str("messages", type="desc", namespace="welcome-messages"),
    )
    async def welcome_messages(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await welcome_messages_service.manager(interaction=interaction, guild_id=guild_id)

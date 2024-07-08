import discord

from app.bot import DiscordBot
from app.services import notifications_twitch as notifications_twitch_service
from app.services.utils import keiko_command
from app.translator import locale_str
from app.types.cogs import Group


class Twitch(
    Group,
    name=locale_str("twitch", type="subgroup", namespace="notifications-twitch"),
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("lives", type="name", namespace="notifications-twitch"),
        description=locale_str("desc", type="desc", namespace="notifications-twitch"),
    )
    async def notifications_twitch(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await notifications_twitch_service.manager(interaction=interaction, guild_id=guild_id)

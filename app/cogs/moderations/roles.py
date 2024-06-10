import discord

from app.bot import DiscordBot
from app.services import default_roles as default_roles_service
from app.services.utils import keiko_command
from app.translator import locale_str
from app.types.cogs import Group


class Roles(
    Group,
    name=locale_str("default", type="subgroup", namespace="default-roles"),
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("roles", type="name", namespace="default-roles"),
        description=locale_str("desc", type="desc", namespace="default-roles"),
    )
    async def default_roles(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await default_roles_service.manager(interaction=interaction, guild_id=guild_id)

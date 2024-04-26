import discord
from discord import app_commands

from app.bot import DiscordBot
from app.services import default_roles as default_roles_service
from app.services.utils import get_available_roles_by_guild, keiko_command
from app.translator import locale_str


class Roles(
    app_commands.Group,
    name=locale_str("default", type="subgroup", namespace="default-roles"),
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        bot.add_listener(self.on_member_join)
        super().__init__()

    async def on_member_join(self, member: discord.Member):
        roles = get_available_roles_by_guild(member.guild)
        if not roles:
            return

        await default_roles_service.set_on_member_join(member)

    @keiko_command(
        name=locale_str("roles", type="name", namespace="default-roles"),
        description=locale_str("desc", type="desc", namespace="default-roles"),
    )
    async def default_roles(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await default_roles_service.manager(interaction=interaction, guild_id=guild_id)

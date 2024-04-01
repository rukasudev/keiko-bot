import discord
from discord import app_commands
from discord.app_commands import locale_str

from app.bot import DiscordBot
from app.services import default_roles as default_roles_service
from app.services.utils import get_available_roles_by_guild


class Roles(app_commands.Group, name=locale_str("default", namespace="commands")):
    def __init__(self, bot: DiscordBot):
        bot.add_listener(self.on_member_join)
        super().__init__()

    async def on_member_join(self, member: discord.Member):
        roles = get_available_roles_by_guild(member.guild)
        if not roles:
            return

        await default_roles_service.set_on_member_join(member)

    @app_commands.command(
        name=locale_str("roles", namespace="commands"),
        description=locale_str("roles-desc", namespace="commands"),
    )
    async def _default_roles(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await default_roles_service.manager(interaction=interaction, guild_id=guild_id)

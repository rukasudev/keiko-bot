import discord
from discord import app_commands
from discord.app_commands import locale_str

from app.bot import DiscordBot
from app.components.embed import response_error_embed
from app.services import default_roles as default_roles_service
from app.services.utils import get_available_roles_by_guild, parse_locale


class Roles(app_commands.Group, name=locale_str("default", namespace="commands")):
    def __init__(self, bot: DiscordBot):
        bot.add_listener(self.on_member_join)
        super().__init__()

    async def on_member_join(self, member: discord.Member):
        roles = get_available_roles_by_guild(member.guild)
        if not roles:
            return

        await default_roles_service.set_default_role(member)

    @app_commands.command(
        name=locale_str("roles", namespace="commands"),
        description=locale_str("roles-desc", namespace="commands"),
    )
    async def _default_roles(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        if not interaction.user.guild_permissions.administrator:
            locale = parse_locale(interaction.locale)
            embed = response_error_embed("command-permission-denied", locale)
            return await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )

        await default_roles_service.manager(interaction=interaction, guild_id=guild_id)

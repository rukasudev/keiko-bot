import discord

from typing import List
from app.services.utils import get_available_roles_by_guild, parse_locale
from app.constants import Commands as constants
from app.data import cogs as cogs_data
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)
from i18n import t

async def set_default_role(member: discord.Member):
    """Command service to set default roles when member join in guild"""
    parameters = cogs_data.find_cog_by_guild_id(member.guild.id, constants.DEFAULT_ROLES_KEY)

    if not parameters:
        return

    default_roles = parameters.get(constants.DEFAULT_ROLES_KEY)
    if not default_roles:
        return

    available_roles = get_available_roles_by_guild(member.guild)
    roles = [role for role in parameters[constants.DEFAULT_ROLES_KEY] if role in available_roles]

    roles_to_add = []
    for role_name in roles:
        role = discord.utils.get(member.guild.roles, name=role_name)
        roles_to_add.append(role)

    await member.add_roles(*roles_to_add)


async def manager(interaction: discord.Interaction, guild_id):
    cogs = cogs_data.find_cog_by_guild_id(guild_id, constants.DEFAULT_ROLES_KEY)
    locale = parse_locale(interaction.locale)

    available_roles = get_available_roles_by_guild(interaction.guild)
    if not cogs:
        if not available_roles:
            error_message = t("errors.command-default-roles-low-permissions.message", locale=locale)
            return await interaction.response.send_message(error_message)

        return await send_command_form_message(interaction, constants.DEFAULT_ROLES_KEY)

    roles = cogs[constants.DEFAULT_ROLES_KEY]
    info = get_not_available_roles(roles, available_roles, locale)

    await send_command_manager_message(interaction, constants.DEFAULT_ROLES_KEY, cogs, info)

def get_not_available_roles(roles: List[str], available_roles: List[str], locale: str) -> str:
    not_available_roles = [role for role in roles if role not in available_roles]
    message = t("errors.command-default-roles-missing-permissions.message", locale=locale)
    return message.replace("$roles", ", ".join(not_available_roles))

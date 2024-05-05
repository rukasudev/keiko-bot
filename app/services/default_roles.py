from typing import Dict, List

import discord

from app.components.buttons import AdditionalButton
from app.components.embed import response_embed, response_error_embed
from app.constants import Commands as constants
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)
from app.services.utils import get_available_roles_by_guild, ml


async def set_on_member_join(member: discord.Member):
    cogs = cache.get_cog_data_or_populate(member.guild.id, constants.DEFAULT_ROLES_KEY)

    if not cogs:
        return

    await set_default_roles(cogs, member.guild, [member])


async def set_on_default_roles_sync(interaction: discord.Interaction):
    await interaction.response.defer()

    cogs = cache.get_cog_data_or_populate(
        interaction.guild.id, constants.DEFAULT_ROLES_KEY
    )

    if not cogs:
        embed = response_error_embed(
            "command-default-roles-disactivated", interaction.locale
        )
        return await interaction.followup.send(
            embed=embed,
            delete_after=10,
            mention_author=True,
        )

    await set_default_roles(cogs, interaction.guild, interaction.guild.members)

    embed = response_embed("commands.roles-sync-response", interaction.locale)
    await interaction.followup.send(embed=embed, ephemeral=True)


async def set_default_roles(
    cogs: Dict[str, str], guild: discord.Guild, members: List[discord.Member]
):
    default_roles_bot = cogs.get(constants.DEFAULT_ROLES_BOT_KEY, [])
    default_roles_user = cogs.get(constants.DEFAULT_ROLES_KEY, [])

    available_roles = get_available_roles_by_guild(guild)
    roles_mapping = {
        constants.DEFAULT_ROLES_BOT_KEY: filter_roles(
            default_roles_bot, available_roles
        ),
        constants.DEFAULT_ROLES_KEY: filter_roles(default_roles_user, available_roles),
    }

    for member in members:
        roles_to_add = get_roles_to_add(member, guild, roles_mapping)
        await member.add_roles(*roles_to_add)


def get_roles_to_add(
    member: discord.Member, guild: discord.Guild, roles_mapping: dict
) -> List[discord.Role]:
    if member.bot:
        role_type = constants.DEFAULT_ROLES_BOT_KEY
    else:
        role_type = constants.DEFAULT_ROLES_KEY

    roles_to_add = []
    for role_name in roles_mapping[role_type]:
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            roles_to_add.append(role)
    return roles_to_add


def filter_roles(roles: List[str], available_roles: List[str]) -> List[str]:
    return [role for role in roles if role in available_roles]


async def manager(interaction: discord.Interaction, guild_id: str):
    cogs = cache.get_cog_data_or_populate(guild_id, constants.DEFAULT_ROLES_KEY)

    available_roles = get_available_roles_by_guild(interaction.guild)
    if cogs == None:
        if not available_roles:
            embed = response_error_embed(
                "command-default-roles-low-permissions", interaction.locale
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        return await send_command_form_message(interaction, constants.DEFAULT_ROLES_KEY)

    roles = cogs[constants.DEFAULT_ROLES_KEY]
    info = get_not_available_roles(roles, available_roles, interaction.locale)
    sync_button = AdditionalButton(
        callback=set_on_default_roles_sync,
        label=ml("buttons.roles-sync.label", interaction.locale),
        desc=ml("buttons.roles-sync.desc", interaction.locale),
        emoji="🔄",
    )

    await send_command_manager_message(
        interaction, constants.DEFAULT_ROLES_KEY, cogs, info, [sync_button]
    )


def get_not_available_roles(
    roles: List[str], available_roles: List[str], locale: str
) -> str:
    not_available_roles = [role for role in roles if role not in available_roles]
    if not not_available_roles:
        return ""

    message = ml(
        "errors.command-default-roles-missing-permissions.message", locale=locale
    )
    return message.replace("$roles", ", ".join(not_available_roles))

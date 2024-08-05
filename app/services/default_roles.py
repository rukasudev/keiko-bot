from typing import Dict, List, Union

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
    cogs = cache.get_cog_data_or_populate(
        interaction.guild.id, constants.DEFAULT_ROLES_KEY
    )

    if not cogs:
        embed = response_error_embed(
            "command-default-roles-disactivated", interaction.locale
        )
        return await interaction.followup.send(
            embed=embed,
            ephemeral=True,
        )

    embed = response_embed(
        "buttons.roles-sync.waiting", interaction.locale, discord.Color.light_grey()
    )
    message = await interaction.followup.send(embed=embed, ephemeral=True)

    await set_default_roles(cogs, interaction.guild, interaction.guild.members)

    embed = response_embed(
        "buttons.roles-sync.response", interaction.locale, discord.Color.green()
    )
    await message.edit(embed=embed)


async def set_default_roles(
    cogs: Dict[str, str], guild: discord.Guild, members: List[discord.Member]
):
    default_roles_bot = cogs.get(constants.DEFAULT_ROLES_BOT_KEY, []).get("values")
    default_roles_user = cogs.get(constants.DEFAULT_ROLES_KEY, []).get("values")

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
    for role_id in roles_mapping[role_type]:
        role = discord.utils.get(guild.roles, id=int(role_id))
        if not role:
            continue

        if role not in member.roles:
            roles_to_add.append(role)

    return roles_to_add


def filter_roles(roles: List[str], available_roles: Dict[str, str]) -> List[str]:
    if isinstance(roles, str):
        return [roles] if roles in available_roles.values() else []
    return [role for role in roles if role in available_roles.values()]


async def manager(interaction: discord.Interaction, guild_id: str):
    cogs = cache.get_cog_data_or_populate(guild_id, constants.DEFAULT_ROLES_KEY, manager=True)

    available_roles = get_available_roles_by_guild(interaction.guild)
    if cogs == None:
        if not available_roles:
            embed = response_error_embed(
                "command-default-roles-low-permissions", interaction.locale
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        return await send_command_form_message(interaction, constants.DEFAULT_ROLES_KEY)

    roles = cogs[constants.DEFAULT_ROLES_KEY].get("values")
    info = get_not_available_roles(roles, available_roles, interaction.locale)
    sync_button = AdditionalButton(
        callback=set_on_default_roles_sync,
        label=ml("buttons.roles-sync.label", interaction.locale),
        desc=ml("buttons.roles-sync.desc", interaction.locale),
        emoji="🔄",
        auto_disable=True,
        defer=True,
    )

    await send_command_manager_message(
        interaction, constants.DEFAULT_ROLES_KEY, cogs, info, [sync_button]
    )


def get_not_available_roles(
    roles: Union[List[str], str], available_roles: Dict[str, str], locale: str
) -> str:
    if isinstance(roles, list):
        not_available_roles = [f"<@&{role}>" for role in roles if role not in available_roles.values()]
    else:
        not_available_roles = [f"<@&{roles}>"] if roles not in available_roles.values() else []

    if not not_available_roles:
        return ""

    message = ml(
        "errors.command-default-roles-missing-permissions.message", locale=locale
    )
    return message.replace("$roles", ", ".join(not_available_roles))

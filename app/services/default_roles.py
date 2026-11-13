import asyncio
from typing import Any, AsyncIterable, Dict, List, Mapping, Optional, Tuple, Union

import discord

from app import logger
from app.components.embed import response_embed, response_error_embed
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.settings import open_feature
from app.core import analytics, cache, members
from app.core.members import every_member
from app.core.utils import at_least, fill, get_available_roles_by_guild, ml


async def set_on_member_join(member: discord.Member):
    cogs = await asyncio.to_thread(
        cache.get_cog_data_or_populate, member.guild.id, constants.DEFAULT_ROLES_KEY
    )

    if not cogs:
        return

    context = ErrorContext.from_member(
        flow="default_roles",
        member=member,
    )

    try:
        await set_default_roles(cogs, member.guild, members.one_by_one([member]))
    except Exception as e:
        logger.error(
            f"Failed to set default roles on member join: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def set_on_default_roles_sync(interaction: discord.Interaction):
    cogs = await asyncio.to_thread(
        cache.get_cog_data_or_populate, interaction.guild.id, constants.DEFAULT_ROLES_KEY
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
        "buttons.roles-sync.waiting", interaction.locale, footer=True, image=True
    )
    message = await interaction.followup.send(embed=embed, ephemeral=True)

    context = ErrorContext.from_interaction(
        flow="default_roles_sync",
        interaction=interaction,
        total_members=interaction.guild.member_count,
    )

    try:
        given = await set_default_roles(
            cogs, interaction.guild, every_member(interaction.guild)
        )

        embed = response_embed(
            "buttons.roles-sync.response", interaction.locale, footer=True, image=True
        )
        embed.description = fill(
            embed.description, members=given["members"], bots=given["bots"]
        )
        await message.edit(embed=embed)
    except Exception as e:
        logger.error(
            f"Failed to sync default roles: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


def build_roles_mapping(cogs: Mapping[str, Any], guild: discord.Guild) -> dict:
    """The roles a member and a bot end up with, filtered to what exists."""
    default_roles_bot_data = cogs.get(constants.DEFAULT_ROLES_BOT_KEY)
    default_roles_bot = default_roles_bot_data.get("values") if isinstance(default_roles_bot_data, dict) else default_roles_bot_data

    default_roles_user_data = cogs.get(constants.DEFAULT_ROLES_KEY)
    default_roles_user = default_roles_user_data.get("values") if isinstance(default_roles_user_data, dict) else default_roles_user_data

    available_roles = get_available_roles_by_guild(guild)
    return {
        constants.DEFAULT_ROLES_BOT_KEY: filter_roles(
            default_roles_bot, available_roles
        ),
        constants.DEFAULT_ROLES_KEY: filter_roles(default_roles_user, available_roles),
    }


async def server_totals(guild: Any) -> Tuple[Optional[int], Optional[int]]:
    """How many members and how many bots the server has, counted one by one; None
    for both past `DEFAULT_ROLES_COUNTED_MEMBERS_MAX` members, which are never read."""
    if not guild:
        return 0, 0
    if not _countable(guild):
        return None, None

    members = bots = 0
    async for member in every_member(guild):
        if member.bot:
            bots += 1
        else:
            members += 1
    return members, bots


async def count_roles_receivers(
    cogs: Mapping[str, Any], guild: Any
) -> Tuple[Optional[int], Optional[int]]:
    """How many members and how many bots a sync would hand a role to: the ones
    still missing one; None for a kind that gets a role on a server past
    `DEFAULT_ROLES_COUNTED_MEMBERS_MAX` members, which are never read."""
    if not guild:
        return 0, 0

    roles_mapping = build_roles_mapping(cogs, guild)
    if not _countable(guild):
        return (
            None if roles_mapping[constants.DEFAULT_ROLES_KEY] else 0,
            None if roles_mapping[constants.DEFAULT_ROLES_BOT_KEY] else 0,
        )

    by_id = {role.id: role for role in guild.roles}
    wanted = {
        kind: [by_id[int(role_id)] for role_id in ids if int(role_id) in by_id]
        for kind, ids in roles_mapping.items()
    }
    members = bots = 0
    async for member in every_member(guild):
        kind = (
            constants.DEFAULT_ROLES_BOT_KEY
            if member.bot
            else constants.DEFAULT_ROLES_KEY
        )
        if not any(role not in member.roles for role in wanted[kind]):
            continue
        if member.bot:
            bots += 1
        else:
            members += 1
    return members, bots


def count_label(count: Optional[int], locale: str) -> str:
    """A count as a screen shows it; one never read is the bound, as a floor."""
    if count is None:
        return at_least(constants.DEFAULT_ROLES_COUNTED_MEMBERS_MAX, locale)
    return str(count)


def _countable(guild: Any) -> bool:
    return (guild.member_count or 0) <= constants.DEFAULT_ROLES_COUNTED_MEMBERS_MAX


async def set_default_roles(
    cogs: Dict[str, str],
    guild: discord.Guild,
    receivers: AsyncIterable[discord.Member],
) -> Dict[str, int]:
    """Give every member the roles their kind gets, and say how many got one."""
    roles_mapping = build_roles_mapping(cogs, guild)
    given = {"members": 0, "bots": 0}

    async for member in receivers:
        roles_to_add = get_roles_to_add(member, guild, roles_mapping)
        try:
            await member.add_roles(*roles_to_add)
        except discord.Forbidden as error:
            analytics.record_permission_failure(
                guild.id, constants.DEFAULT_ROLES_KEY, error
            )
            raise
        if roles_to_add:
            given["bots" if member.bot else "members"] += 1
            analytics.record_value(guild.id, constants.DEFAULT_ROLES_KEY)

    return given


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
    if not roles or not available_roles:
        return []
    if isinstance(roles, str):
        return [roles] if roles in available_roles.values() else []
    return [role for role in roles if role in available_roles.values()]


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, constants.DEFAULT_ROLES_KEY)


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

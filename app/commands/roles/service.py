"""Default roles: the roles new members and bots get, on join and on a sync."""

from dataclasses import dataclass, replace
from typing import Any, AsyncIterable, Dict, List, Mapping, Optional, Tuple, Union

import discord

from app import logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.core import analytics, cache, members
from app.core.commands import MemberJoined
from app.core.facts import Facts, facts
from app.core.members import every_member
from app.core.utils import at_least, ensure_list, get_available_roles_by_guild
from app.exceptions import ErrorContext
from app.forms import open_feature
from app.forms.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.forms.form.form_state import Answer
from app.forms.form.responses.styles import empty_value
from app.ui.embed import response_embed, response_error_embed

SavedRoles = Union[List[str], str, None]


def command_facts(**values: Any) -> Facts:
    """The facts this command's code passes its copy, each under its address."""
    return facts("default_roles", **values)


@dataclass(frozen=True)
class DefaultRoles:
    """The role ids a member and a bot get, among the ones the bot can assign."""

    members: Tuple[str, ...]
    bots: Tuple[str, ...]

    def of(self, member: discord.Member) -> Tuple[str, ...]:
        """The role ids the kind of `member` gets."""
        return self.bots if member.bot else self.members


@dataclass(frozen=True)
class RolesGiven:
    """How many members and how many bots got at least one role."""

    members: int
    bots: int


async def give_roles(joined: MemberJoined) -> None:
    """Give the member who joined their roles, when Keiko can give a role at all."""
    if get_available_roles_by_guild(joined.member.guild):
        await set_on_member_join(joined.member)


async def set_on_member_join(member: discord.Member) -> None:
    """Give a member who just joined the roles their kind gets."""
    cogs = await cache.read_cog_data(member.guild.id, "default_roles")

    if not cogs:
        return

    context = ErrorContext.from_member(
        flow="default_roles",
        member=member,
    )

    try:
        await set_default_roles(cogs, member.guild, members.one_by_one([member]))
    except Exception as error:
        logger.error(
            "Failed to set default roles on member join: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def set_on_default_roles_sync(interaction: discord.Interaction) -> None:
    """Give everyone on the server the roles their kind gets, and say how many."""
    guild = interaction.guild
    if guild is None:
        return

    locale = str(interaction.locale)
    cogs = await cache.read_cog_data(guild.id, "default_roles")

    if not cogs:
        embed = response_error_embed("default_roles.ui.errors.deactivated", locale)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    embed = response_embed("default_roles.ui.responses.syncing", locale, image=True)
    message = await interaction.followup.send(embed=embed, ephemeral=True, wait=True)

    context = ErrorContext.from_interaction(
        flow="default_roles_sync",
        interaction=interaction,
        total_members=guild.member_count,
    )

    try:
        given = await set_default_roles(cogs, guild, every_member(guild))

        embed = response_embed(
            "default_roles.ui.responses.synced",
            locale,
            image=True,
            facts=command_facts(members=given.members, bots=given.bots),
        )
        await message.edit(embed=embed)
    except Exception as error:
        logger.error(
            f"Failed to sync default roles: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


def saved_roles(entry: Any) -> SavedRoles:
    """The role ids a saved entry holds, from its envelope or bare."""
    roles: SavedRoles = entry.get("values") if isinstance(entry, Mapping) else entry
    return roles


def assignable_roles(cogs: Mapping[str, Any], guild: discord.Guild) -> DefaultRoles:
    """The roles a member and a bot end up with, filtered to what exists."""
    available_roles = get_available_roles_by_guild(guild)
    member_roles = saved_roles(cogs.get("default_roles"))
    bot_roles = saved_roles(cogs.get(constants.DEFAULT_ROLES_BOT_KEY))
    return DefaultRoles(
        members=tuple(filter_roles(member_roles, available_roles)),
        bots=tuple(filter_roles(bot_roles, available_roles)),
    )


async def server_totals(
    guild: Optional[discord.Guild],
) -> Tuple[Optional[int], Optional[int]]:
    """How many members and bots the server has, None for both past the bound."""
    if not guild:
        return 0, 0
    if not _countable(guild):
        return None, None

    people = bots = 0
    async for member in every_member(guild):
        if member.bot:
            bots += 1
        else:
            people += 1
    return people, bots


async def count_roles_receivers(
    cogs: Mapping[str, Any], guild: Optional[discord.Guild]
) -> Tuple[Optional[int], Optional[int]]:
    """How many members and bots a sync would hand a role to, None past the bound."""
    if not guild:
        return 0, 0

    roles = assignable_roles(cogs, guild)
    if not _countable(guild):
        return (None if roles.members else 0, None if roles.bots else 0)

    by_id = {role.id: role for role in guild.roles}
    wanted = {
        is_bot: [by_id[int(role_id)] for role_id in ids if int(role_id) in by_id]
        for is_bot, ids in ((False, roles.members), (True, roles.bots))
    }
    people = bots = 0
    async for member in every_member(guild):
        if not any(role not in member.roles for role in wanted[bool(member.bot)]):
            continue
        if member.bot:
            bots += 1
        else:
            people += 1
    return people, bots


def count_label(count: Optional[int], locale: str) -> str:
    """A count as a screen shows it; one never read is the bound, as a floor."""
    if count is None:
        return at_least(constants.DEFAULT_ROLES_COUNTED_MEMBERS_MAX, locale)
    return str(count)


def _countable(guild: discord.Guild) -> bool:
    return (guild.member_count or 0) <= constants.DEFAULT_ROLES_COUNTED_MEMBERS_MAX


async def set_default_roles(
    cogs: Mapping[str, Any],
    guild: discord.Guild,
    receivers: AsyncIterable[discord.Member],
) -> RolesGiven:
    """Give every member the roles their kind gets, and say how many got one."""
    roles = assignable_roles(cogs, guild)
    people = bots = 0

    async for member in receivers:
        roles_to_add = get_roles_to_add(member, guild, roles)
        try:
            await member.add_roles(*roles_to_add)
        except discord.Forbidden as error:
            analytics.record_permission_failure(guild.id, "default_roles", error)
            raise

        if roles_to_add:
            if member.bot:
                bots += 1
            else:
                people += 1
            analytics.record_value(guild.id, "default_roles")

    return RolesGiven(members=people, bots=bots)


def get_roles_to_add(
    member: discord.Member, guild: discord.Guild, roles: DefaultRoles
) -> List[discord.Role]:
    """The roles of the kind of `member` it does not hold yet."""
    roles_to_add = []
    for role_id in roles.of(member):
        role = discord.utils.get(guild.roles, id=int(role_id))
        if not role:
            continue

        if role not in member.roles:
            roles_to_add.append(role)

    return roles_to_add


def filter_roles(roles: SavedRoles, available_roles: Dict[str, str]) -> List[str]:
    """The saved role ids the bot can still assign."""
    if not available_roles:
        return []
    return [role for role in ensure_list(roles) if role in available_roles.values()]


async def manager(interaction: discord.Interaction) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, "default_roles")


def get_not_available_roles(
    roles: SavedRoles, available_roles: Dict[str, str], locale: str
) -> str:
    """The warning naming the saved roles the bot can no longer assign, or none."""
    not_available_roles = [
        f"<@&{role}>"
        for role in ensure_list(roles)
        if role not in available_roles.values()
    ]

    if not not_available_roles:
        return ""

    roles = ", ".join(not_available_roles)
    return FEATURE.panel_info_copy(locale, command_facts(roles=roles))[1]


class DefaultRolesFeature(GenericCogFeature):
    """Roles for new members and bots, with a sync button on the panel."""

    panel_asides = {"sync": "🔄"}
    fills_panel_info = True

    def __init__(self) -> None:
        super().__init__("default_roles")

    async def open(self, context: OpenContext) -> Opened:
        """The document, or a refusal when the bot can assign no role at all."""
        opened = await super().open(context)
        available = get_available_roles_by_guild(context.guild) if context.guild else {}

        if opened.document is None:
            if not available:
                return Opened(refusal="default_roles.ui.errors.low_permissions")
            return replace(
                opened, values=await _server_totals(context.guild, context.locale)
            )
        roles = saved_roles(opened.document.get("default_roles"))
        warning = get_not_available_roles(roles, available, context.locale)
        return replace(opened, panel=replace(opened.panel, info=warning))

    def to_document(self, answers: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        """The roles to save; whether to give them out now is never stored."""
        document = super().to_document(answers, locale)
        document.pop("sync_existing", None)
        return document

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Save the roles, then give them to who is already there when asked."""
        result = await super().commit_setup(payload, context)
        wanted = payload["answers"].get("sync_existing")

        if wanted is not None and wanted.raw and context.guild is not None:
            guild = context.guild
            self.in_background(
                set_default_roles(
                    dict(result.document or {}), guild, every_member(guild)
                ),
                context,
            )
        return result

    def asides(self) -> Mapping[str, AsideAction]:
        """The statistics, and the sync, run once a minute at most."""

        async def sync(interaction: Any, _responses: Any) -> None:
            await set_on_default_roles_sync(interaction)

        return {
            **super().asides(),
            "sync": AsideAction(
                sync,
                defer=True,
                cooldown=view_constants.SYNC_COOLDOWN_SECONDS,
                confirm="default_roles.ui.responses.sync_confirm",
                confirm_values=_roles_of,
            ),
        }


async def _server_totals(guild: Any, locale: str) -> dict[str, str]:
    members, bots = await server_totals(guild)
    return {
        "members_count": count_label(members, locale),
        "bots_count": count_label(bots, locale),
    }


async def _roles_of(
    document: Mapping[str, Any], locale: str, guild: Any = None
) -> Mapping[str, str]:
    members, bots = await count_roles_receivers(document, guild)
    return {
        "members": _mentions(document.get("default_roles"), locale),
        "bots": _mentions(document.get(constants.DEFAULT_ROLES_BOT_KEY), locale),
        "members_count": count_label(members, locale),
        "bots_count": count_label(bots, locale),
    }


def _mentions(entry: Any, locale: str) -> str:
    values = saved_roles(entry)
    if isinstance(values, (list, tuple)):
        roles = [str(value) for value in values]
    else:
        roles = [str(values)] if values else []
    joined = ", ".join(f"<@&{role}>" for role in roles)
    return joined or empty_value(locale)


FEATURE = DefaultRolesFeature()

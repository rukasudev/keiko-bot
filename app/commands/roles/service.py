"""Auto roles: the roles new members and bots get, on join and on a sync, and the
roles by time in the server, a ladder each member climbs once a day."""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone, tzinfo
from itertools import groupby
from typing import (
    Any,
    AsyncIterable,
    Collection,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import discord

from app import bot, logger
from app.constants import Commands as constants
from app.constants import DiscordLimits
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.core import analytics, cache, members, trace
from app.core.calendar import calendar_span, span_days, span_end, span_parts
from app.core.cogs import is_feature_on
from app.core.commands import MemberJoined
from app.core.dates import (
    elapsed_label,
    full_date_label,
    next_day_start,
    utc_now,
    zone_or_utc,
)
from app.core.facts import Facts, facts
from app.core.members import every_member
from app.core.servers import server_timezone
from app.core.utils import (
    at_least,
    ensure_list,
    fill,
    givable_role_ids,
    join_names,
    ml,
)
from app.data import data
from app.data.keiko.schedules import schedule_id, schedule_subject
from app.exceptions import ErrorContext, ServerSettingsMissing
from app.forms import document_values, item_values, items_of
from app.forms.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    FollowUp,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.forms.form.form_state import Answer
from app.forms.form.responses.styles import empty_value
from app.forms.form.responses.summary import unwrap
from app.ui.embed import response_card, response_embed, response_error_embed
from app.ui.records import RecordsBrowser

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
    if givable_role_ids(joined.member.guild):
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


def assignable_roles(cogs: Mapping[str, Any], guild: discord.Guild) -> DefaultRoles:
    """The roles a member and a bot end up with, filtered to what exists."""
    available_roles = givable_role_ids(guild)
    saved = document_values(cogs)
    member_roles = saved.get("default_roles")
    bot_roles = saved.get(constants.DEFAULT_ROLES_BOT_KEY)
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


def filter_roles(roles: SavedRoles, available_roles: Collection[str]) -> List[str]:
    """The saved role ids the bot can still assign."""
    return [role for role in ensure_list(roles) if role in available_roles]


def get_not_available_roles(
    roles: SavedRoles, available_roles: Collection[str], locale: str
) -> str:
    """The warning naming the saved roles the bot can no longer assign, or none."""
    not_available_roles = [
        f"<@&{role}>" for role in ensure_list(roles) if role not in available_roles
    ]

    if not not_available_roles:
        return ""

    roles = ", ".join(not_available_roles)
    return FEATURE.panel_info_copy(locale, command_facts(roles=roles))[1]


class DefaultRolesFeature(GenericCogFeature):
    """Roles for new members and bots, with a sync button on the panel, and the roles
    by time each member climbs once a day."""

    panel_asides = {"sync": "🔄"}
    fills_panel_info = True

    def __init__(self) -> None:
        super().__init__("default_roles")

    async def open(self, context: OpenContext) -> Opened:
        """The document, or a refusal when the bot can assign no role at all."""
        opened = await super().open(context)
        available = givable_role_ids(context.guild) if context.guild else set()

        if opened.document is None:
            if not available:
                return Opened(refusal="default_roles.ui.errors.low_permissions")
            return replace(
                opened, values=await _server_totals(context.guild, context.locale)
            )
        document = dict(opened.document)
        document.setdefault("role_ladder", _no_rungs())
        saved = document_values(document).get("default_roles")
        ladder = [rung.role for rung in rungs_of(document)]
        roles = [*ensure_list(saved), *ladder]
        warning = get_not_available_roles(roles, available, context.locale)
        return replace(
            opened, document=document, panel=replace(opened.panel, info=warning)
        )

    def to_document(self, answers: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        """The roles to save; whether to give them out now is never stored."""
        document = super().to_document(answers, locale)
        document.pop("sync_existing", None)
        return document

    def item_document(self, item: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        """A rung as the document stores it, counted from now until a yes clears its
        start."""
        return {**super().item_document(item, locale), "state": _started_now()}

    def edited_state(
        self, old: Mapping[str, Any], new: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        """A time role keeps its start while its time stays the same span, and counts
        from now once it changes."""
        if not _same_time(_time_of(old), _time_of(new)):
            return _started_now()
        return dict(old.get("state") or {})

    async def before_setup(
        self,
        document: dict[str, Any],
        _answers: Mapping[str, Answer],
        _context: CommitContext,
    ) -> dict[str, Any]:
        """A setup saves the ladder even with no rung, so the panel offers its Add, and
        counts every rung from now until a yes clears its start."""
        rungs = [
            {**item, "state": _started_now()}
            for item in items_of(document, "role_ladder")
        ]
        return {**document, "role_ladder": _listed(_shortest_first(rungs))}

    async def save_items(
        self, context: CommitContext, saved: Any, items: List[dict[str, Any]]
    ) -> dict[str, Any]:
        """Store the time roles, the shortest time first."""
        return await super().save_items(context, saved, _shortest_first(items))

    async def commit(
        self, kind: str, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Write the change, then keep the daily pass due only while a ladder runs."""
        result = await super().commit(kind, payload, context)
        now = utc_now()
        await self.best_effort(arm_ladder(context.guild_id, now), context)
        return result

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Save the roles, give them to who is already there when asked, and ask
        about the members already past a rung."""
        result = await super().commit_setup(payload, context)
        wanted = payload["answers"].get("sync_existing")
        document = dict(result.document or {})

        if wanted is not None and wanted.raw and context.guild is not None:
            guild = context.guild
            self.in_background(
                set_default_roles(document, guild, every_member(guild)), context
            )
        if not rungs_of(document):
            return result
        asks = await self.best_effort(ladder_question(document, context), context)
        return replace(result, asks=asks)

    async def commit_add_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Save the rung, then ask about the members already past it."""
        result = await super().commit_add_item(payload, context)
        added = self.item_document(payload["answers"], context.locale)
        return await self.asked_about(result, _time_of(added), context)

    async def commit_edit_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Save the rung, and ask like a new one when its time changed."""
        edited = self.shown_item(context, payload.get("index"))
        result = await super().commit_edit_item(payload, context)
        time = _time_of(self.item_document(payload["answers"], context.locale))
        if _same_time(time, _time_of(edited)):
            return result
        return await self.asked_about(result, time, context)

    async def asked_about(
        self, result: CommitResult, time: Optional[str], context: CommitContext
    ) -> CommitResult:
        """The save with the question about the members already past the rung of
        `time`, one item of the list."""
        document = dict(result.document or {})
        items = items_of(document, "role_ladder")
        position = next(
            (
                index
                for index, item in enumerate(items)
                if _same_time(_time_of(item), time)
            ),
            None,
        )
        if position is None:
            return result
        target = f"role_ladder${position}"
        asks = await self.best_effort(
            ladder_question(document, context, time, target), context
        )
        return replace(result, asks=asks)

    def asides(self) -> Mapping[str, AsideAction]:
        """The statistics, the sync, run once a minute at most, and the question a
        rung asks: every rung after the setup, one rung on the panel."""

        async def sync(interaction: Any, _responses: Any) -> None:
            await set_on_default_roles_sync(interaction)

        async def ladder(interaction: Any, _responses: Any) -> None:
            await self.give_rungs_now(interaction, None)

        async def rung(interaction: Any, responses: Any) -> None:
            await self.give_rungs_now(interaction, _time_shown(responses))

        return {
            **super().asides(),
            "sync": AsideAction(
                sync,
                defer=True,
                cooldown=view_constants.SYNC_COOLDOWN_SECONDS,
                confirm="default_roles.ui.responses.sync_confirm",
                confirm_values=_roles_of,
            ),
            "ladder": AsideAction(
                ladder, confirm="default_roles.ui.responses.ladder_confirm"
            ),
            "rung": AsideAction(
                rung, confirm="default_roles.ui.responses.ladder_confirm"
            ),
        }

    async def give_rungs_now(
        self, interaction: discord.Interaction, time: Optional[str]
    ) -> None:
        """Say the roles by time are on their way, then, outside the click, clear the
        start of the rung of `time` (every rung when None) and give its role to the
        members already past it."""
        guild = interaction.guild
        if guild is None:
            return

        locale = str(interaction.locale)
        guild_id = str(guild.id)
        config = await data().guild_cogs.find_cog_by_guild_id(guild_id, self.key)
        if config is None or not is_feature_on(config):
            embed = response_error_embed(
                "default_roles.ui.errors.ladder_deactivated", locale
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = response_embed(
            "default_roles.ui.responses.ladder_giving", locale, image=True
        )
        message = await interaction.followup.send(
            embed=embed, ephemeral=True, wait=True
        )
        context = CommitContext(
            guild_id=guild_id,
            user_id=str(interaction.user.id),
            locale=locale,
            document=config,
            when=interaction.created_at,
            guild=guild,
        )
        self.in_background(self.climb_from_scratch(message, time, context), context)

    async def climb_from_scratch(
        self, message: Any, time: Optional[str], context: CommitContext
    ) -> None:
        """Clear the asked rungs' start, then give each member past them their role,
        and say how many climbed; a failure is told with the generic error."""
        try:
            started = started_over(context.document, time)
            document = await self.save_items(
                context, context.document.get("role_ladder"), started
            )
            zone = await ladder_zone(context.guild_id)
            now = utc_now()
            climbed = await climb_ladder(context.guild, document, now, zone)
        except Exception:
            await message.edit(embed=response_error_embed("generic", context.locale))
            raise

        embed = response_embed(
            "default_roles.ui.responses.ladder_given",
            context.locale,
            image=True,
            facts=command_facts(climbed=climbed),
        )
        await message.edit(embed=embed)


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
    saved = document_values(document)
    return {
        "members": _mentions(saved.get("default_roles"), locale),
        "bots": _mentions(saved.get(constants.DEFAULT_ROLES_BOT_KEY), locale),
        "members_count": count_label(members, locale),
        "bots_count": count_label(bots, locale),
    }


def _mentions(values: SavedRoles, locale: str) -> str:
    if isinstance(values, (list, tuple)):
        roles = [str(value) for value in values]
    else:
        roles = [str(values)] if values else []
    joined = ", ".join(f"<@&{role}>" for role in roles)
    return joined or empty_value(locale)


@dataclass(frozen=True)
class Rung:
    """One rung of the ladder: the time in the server it needs, stored as a span such
    as `P5M`, the role it gives, and since when a member who reaches it counts, None
    for any member."""

    time: str
    role: str
    since: Optional[datetime] = None

    def reached_on(self, joined: date) -> date:
        """The day a member who joined on `joined` completes the rung's time."""
        amount, unit = span_parts(self.time) or (0, "days")
        return span_end(joined, amount, unit)


@dataclass(frozen=True)
class Climber:
    """A member a yes gives a time role, and the day they joined the server."""

    id: str
    joined: date


@dataclass(frozen=True)
class Climb:
    """The ladder roles one member gets and loses on a pass."""

    give: Tuple[discord.Role, ...]
    take: Tuple[discord.Role, ...]


def rungs_of(config: Optional[Mapping[str, Any]]) -> Tuple[Rung, ...]:
    """The saved rungs, the shortest time first."""
    rungs = []
    for item in items_of(config or {}, "role_ladder"):
        time, roles = _time_of(item), ensure_list(item_values(item).get("role"))
        if time is None or not roles:
            continue
        since = (item.get("state") or {}).get("since")
        started = _aware(since) if isinstance(since, datetime) else None
        rungs.append(Rung(time, str(roles[0]), started))
    return tuple(sorted(rungs, key=lambda rung: span_days(rung.time)))


def started_over(
    config: Mapping[str, Any], time: Optional[str]
) -> List[dict[str, Any]]:
    """The saved rungs with the start of the rung of `time` cleared, every rung's when
    None, so each member past it counts."""
    return [
        {**item, "state": {**(item.get("state") or {}), "since": None}}
        if time is None or _same_time(_time_of(item), time)
        else item
        for item in items_of(config, "role_ladder")
    ]


async def ladder_question(
    document: Mapping[str, Any],
    context: CommitContext,
    time: Optional[str] = None,
    target: str = "",
) -> Optional[FollowUp]:
    """The question about the members already past the rung of `time`, every rung
    when None, with who a yes would give its role; none when nobody is."""
    guild = context.guild
    if guild is None:
        return None
    zone = await ladder_zone(context.guild_id)
    now = utc_now()
    found = await climbers_by_role(document, guild, now, zone, time)

    if found is not None and not found:
        return None
    aside = "ladder" if time is None else "rung"
    if found is None:
        uncounted = fill(
            ml("default_roles.ui.texts.ladder_uncounted", context.locale),
            members_count=count_label(None, context.locale),
        )
        uncut: Dict[str, str | bool] = {
            "groups": uncounted,
            "roles_left_out": "",
            "members_left_out": False,
        }
        return FollowUp(aside, uncut, target)
    today = now.astimezone(zone).date()
    asked = climber_groups(found, today, context.locale)
    if not asked["roles_left_out"] and not asked["members_left_out"]:
        return FollowUp(aside, asked, target)
    browser = climbers_browser(found, guild, today, context.locale)
    return FollowUp(aside, asked, target, browser.send)


def climber_groups(
    found: Mapping[str, Sequence[Climber]], today: date, locale: str
) -> Dict[str, str | bool]:
    """The question's facts: its blocks, one for each of the first
    `ViewConstants.QUESTION_GROUPS_SHOWN` time roles a yes gives, each its mention and
    count over a line per member, when they joined and their time in the server, as
    many as fit the question's card and `QUESTION_GROUP_ITEMS_SHOWN` at most, and how
    many more; the roles it leaves out, named; and whether a block left anyone out."""
    limit = view_constants.QUESTION_GROUP_ITEMS_SHOWN
    shown_roles = dict(list(found.items())[: view_constants.QUESTION_GROUPS_SHOWN])
    left_out = join_names(
        [f"<@&{role}>" for role in found if role not in shown_roles], locale
    )
    lines = {
        role: [_climber_line(climber, today, locale) for climber in climbers[:limit]]
        for role, climbers in shown_roles.items()
    }
    asked = response_card(
        "default_roles.ui.responses.ladder_confirm",
        locale,
        image=True,
        facts=command_facts(groups="", roles_left_out=left_out, members_left_out=True),
    )
    budget = DiscordLimits.LAYOUT_TEXT - asked.content_length()

    def written(shown: int) -> str:
        blocks = []
        for role, climbers in shown_roles.items():
            hidden = len(climbers) - shown
            more = (
                [fill(ml("texts.and_more", locale), count=hidden)] if hidden > 0 else []
            )
            first = [*lines[role][:shown], *more]
            blocks.append(_climber_block(role, len(climbers), first, locale))
        return "\n\n".join(blocks)

    shown = next(
        (count for count in range(limit, 0, -1) if len(written(count)) <= budget), 0
    )
    return {
        "groups": written(shown),
        "roles_left_out": left_out,
        "members_left_out": any(
            len(climbers) > shown for climbers in shown_roles.values()
        ),
    }


def _climber_block(role: str, count: int, lines: Sequence[str], locale: str) -> str:
    return fill(
        ml("default_roles.ui.texts.ladder_group", locale),
        role=role,
        count=count,
        climbers="\n".join(lines),
    )


def _climber_line(climber: Climber, today: date, locale: str) -> str:
    return fill(
        ml("default_roles.ui.texts.ladder_member", locale),
        user=f"<@{climber.id}>",
        date=full_date_label(climber.joined, locale),
        time_in_server=elapsed_label(climber.joined, today, locale),
    )


def climbers_browser(
    found: Mapping[str, Sequence[Climber]],
    guild: discord.Guild,
    today: date,
    locale: str,
) -> RecordsBrowser:
    """The browser of every member the question counted, filtered by role: ten a
    page at most, fewer when they would not fit its field, under the role a yes
    gives them."""
    records = [
        (role, climber) for role, climbers in found.items() for climber in climbers
    ]

    async def fetch(
        _interaction: discord.Interaction, chosen: Optional[str]
    ) -> List[Tuple[str, Climber]]:
        return [record for record in records if chosen in (None, record[0])]

    def to_fields(
        shown: List[Tuple[str, Climber]], _interaction: discord.Interaction
    ) -> Dict[str, str]:
        return _climber_pages(shown, today, locale)

    options = {}
    for role in found:
        given = guild.get_role(int(role))
        options[role] = given.name if given is not None else role

    return RecordsBrowser(
        fetch,
        to_fields,
        sep=view_constants.GROUPED_RECORDS_PAGE_SIZE,
        copy="default_roles.ui.records.climbers",
        locale=locale,
        options=options,
        named=False,
    )


def _climber_pages(
    records: Sequence[Tuple[str, Climber]], today: date, locale: str
) -> Dict[str, str]:
    limit = view_constants.COMPOSITION_PREVIEW_LIMIT
    counts = Counter(role for role, _ in records)
    lines = [(role, _climber_line(climber, today, locale)) for role, climber in records]
    pages = []
    start = 0

    while start < len(lines):
        end = min(start + limit, len(lines))
        page = _climber_page(lines[start:end], counts, locale)
        while end - start > 1 and len(page) > DiscordLimits.EMBED_FIELD_VALUE:
            end -= 1
            page = _climber_page(lines[start:end], counts, locale)
        pages.append(page)
        start = end
    return {str(number): page for number, page in enumerate(pages, start=1)}


def _climber_page(
    lines: Sequence[Tuple[str, str]], counts: Mapping[str, int], locale: str
) -> str:
    return "\n\n".join(
        _climber_block(role, counts[role], [line for _, line in grouped], locale)
        for role, grouped in groupby(lines, key=lambda line: line[0])
    )


def ladder_schedule(guild_id: Any) -> str:
    """The schedule a server's daily pass waits under."""
    return schedule_id("role_ladder", guild_id)


async def ladder_zone(guild_id: str) -> tzinfo:
    """The time zone whose day boundary starts a server's pass, UTC when unsaved."""
    try:
        return zone_or_utc(await server_timezone(guild_id))
    except ServerSettingsMissing:
        return timezone.utc


async def arm_ladder(
    guild_id: str, now: datetime, keep_due: bool = True
) -> Optional[Mapping[str, Any]]:
    """Keep the server's pass due while its ladder is on, at its next day boundary,
    or when `keep_due` still at a time already due, so a save just after midnight
    never puts off that day's pass, and hand back its settings; drop the pass and hand
    back nothing otherwise."""
    config = await data().guild_cogs.find_cog_by_guild_id(guild_id, "default_roles")
    entry = ladder_schedule(guild_id)

    if not is_feature_on(config) or not rungs_of(config):
        await data().keiko_schedules.delete_schedule(entry)
        return None
    if keep_due:
        armed = await data().keiko_schedules.find_schedule(entry)
        if armed is not None and _aware(armed["due_at"]) <= now:
            return config
    zone = await ladder_zone(guild_id)
    due_at = next_day_start(zone, now)
    await data().keiko_schedules.upsert_schedule(
        entry, "role_ladder", None, due_at=due_at
    )
    return config


async def climb_due_ladders(due: Sequence[Mapping[str, Any]]) -> None:
    """Run the pass of every server whose day began, each on its own, a few servers
    at a time."""
    now = utc_now()
    guild_ids = [schedule_subject(dict(entry))[0] for entry in due]
    await trace.fan_out(
        "role_ladder",
        [(guild_id, climb_server(guild_id, now)) for guild_id in guild_ids],
        at_once=constants.DEFAULT_ROLES_LADDER_SERVERS_AT_ONCE,
    )


async def climb_server(guild_id: str, now: datetime) -> None:
    """One server's pass: due again tomorrow, then each member on their rung."""
    config = await arm_ladder(guild_id, now, keep_due=False)
    guild = bot.get_guild(int(guild_id)) if config else None
    if config is None or guild is None:
        return

    zone = await ladder_zone(guild_id)
    climbed = await climb_ladder(guild, config, now, zone)
    logger.info(
        f"Role ladder of guild {guild_id}: {climbed} member(s) changed rung",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def climbers_by_role(
    config: Mapping[str, Any],
    guild: discord.Guild,
    now: datetime,
    zone: tzinfo,
    time: Optional[str] = None,
) -> Optional[dict[str, list[Climber]]]:
    """The members a yes would give the role of the rung of `time`, of any rung when
    None, by the role they get, shortest time first; None past the bound."""
    if not _countable(guild):
        return None

    plan = _ladder_plan({"role_ladder": _listed(started_over(config, time))}, guild)
    asked = [
        rung.role for rung in plan.rungs if time is None or _same_time(rung.time, time)
    ]
    today = now.astimezone(zone).date()
    found: dict[str, list[Climber]] = {role: [] for role in dict.fromkeys(asked)}
    async for member in every_member(guild):
        climb = climb_of(member, plan, today, zone)
        given = [str(role.id) for role in climb.give] if climb is not None else []
        role = next((role for role in given if role in found), None)
        if role is not None and member.joined_at is not None:
            joined = member.joined_at.astimezone(zone).date()
            found[role].append(Climber(str(member.id), joined))
    return {role: climbers for role, climbers in found.items() if climbers}


async def climb_ladder(
    guild: discord.Guild,
    config: Mapping[str, Any],
    now: datetime,
    zone: tzinfo,
) -> int:
    """Give every member the role of the highest rung they earned and take the other
    ladder roles, one member at a time; how many changed."""
    plan = _ladder_plan(config, guild)
    if plan.skipped:
        logger.warn(
            f"Role ladder of guild {guild.id} skips the roles Keiko cannot give: "
            f"{', '.join(plan.skipped)}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )

    today = now.astimezone(zone).date()
    climbed = 0
    async for member in every_member(guild):
        climb = climb_of(member, plan, today, zone)
        if climb is None:
            continue
        try:
            if climb.give:
                await member.add_roles(*climb.give)
            if climb.take:
                await member.remove_roles(*climb.take)
        except discord.Forbidden as error:
            analytics.record_permission_failure(guild.id, "default_roles", error)
            raise
        except discord.HTTPException as error:
            logger.warn(
                f"Role ladder of guild {guild.id} left member {member.id} as it was: "
                f"{type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            continue
        climbed += 1
        analytics.record_value(guild.id, "default_roles")
    return climbed


@dataclass(frozen=True)
class LadderPlan:
    """The rungs of one pass, the roles Keiko can give, and the ones it skips."""

    rungs: Tuple[Rung, ...]
    roles: Mapping[str, discord.Role]
    skipped: Tuple[str, ...]


def climb_of(
    member: discord.Member, plan: LadderPlan, today: date, zone: tzinfo
) -> Optional[Climb]:
    """What a pass changes for one member: the role of the highest rung reached that
    they hold or reached after its start, given, and every other ladder role taken;
    None for no change, and for a member whose top rung Keiko cannot give."""
    if member.bot or member.joined_at is None:
        return None
    joined = member.joined_at.astimezone(zone).date()
    reached = [rung for rung in plan.rungs if rung.reached_on(joined) <= today]
    if not reached or reached[-1].role not in plan.roles:
        return None

    held = {str(role.id) for role in member.roles}
    earned = [
        rung
        for rung in reached
        if rung.role in plan.roles
        and (rung.role in held or _reached_after_start(rung, joined, zone))
    ]
    if not earned:
        return None
    top = earned[-1].role
    give = () if top in held else (plan.roles[top],)
    others = {rung.role for rung in plan.rungs} - {top}
    take = tuple(
        plan.roles[role] for role in sorted(others & held) if role in plan.roles
    )
    return Climb(give, take) if give or take else None


def _reached_after_start(rung: Rung, joined: date, zone: tzinfo) -> bool:
    if rung.since is None:
        return True
    return rung.reached_on(joined) > rung.since.astimezone(zone).date()


def _ladder_plan(config: Mapping[str, Any], guild: discord.Guild) -> LadderPlan:
    rungs = rungs_of(config)
    givable = givable_role_ids(guild)
    roles = {str(role.id): role for role in guild.roles if str(role.id) in givable}
    skipped = tuple(
        dict.fromkeys(rung.role for rung in rungs if rung.role not in roles)
    )
    return LadderPlan(rungs, roles, skipped)


def _time_of(item: Mapping[str, Any]) -> Optional[str]:
    time = item_values(item).get("time")
    return str(time) if span_parts(time) else None


def _same_time(time: Optional[str], other: Optional[str]) -> bool:
    if time is None or other is None:
        return time is other
    return calendar_span(time) == calendar_span(other)


def _shortest_first(items: List[dict[str, Any]]) -> List[dict[str, Any]]:
    return sorted(items, key=lambda item: span_days(_time_of(item)))


def _time_shown(responses: Sequence[Mapping[str, Any]]) -> Optional[str]:
    shown = next((row for row in responses if row.get("key") == "time"), None)
    return None if shown is None else str(unwrap(shown))


def _started_now() -> dict[str, Any]:
    return {"since": utc_now()}


def _listed(items: List[dict[str, Any]]) -> dict[str, Any]:
    return {"style": "composition", "values": items}


def _no_rungs() -> dict[str, Any]:
    return _listed([])


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


FEATURE = DefaultRolesFeature()

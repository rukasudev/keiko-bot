"""The /admin operator screens: the overview, the log, the operator ids, the erasure."""

import resource
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    TypedDict,
)

import discord

from app.bot import DiscordBot
from app.constants import Commands, DBConfigs, KeikoIcons, Style
from app.core import analytics, analytics_reports, cache, logs_archive
from app.core.threads import to_thread
from app.core.utils import format_datetime_output, format_relative_time
from app.data import data
from app.forms.features import feature_keys


@dataclass(frozen=True)
class DiscordShows:
    """What Discord's cache tells of Keiko now."""

    uptime: str
    last_restart: str
    latency_ms: int
    status: str
    activity_name: Optional[str]
    total_channels: int
    largest_guild_name: str
    largest_guild_members: int
    memory_mb: float


@dataclass(frozen=True)
class KeikoStored:
    """What Keiko recorded of its guilds, its commands and its integrations."""

    active_guilds: int
    top_owner_id: Optional[str]
    top_owner_count: int
    feature_adoption: Dict[str, int]
    newest_guild_id: Optional[str]
    newest_guild_created: Optional[datetime]
    monthly_growth: int
    command_calls: Dict[str, int]
    command_errors: Dict[str, int]
    total_calls: int
    error_rate: Optional[float]
    redis_keys: int
    twitch_streamers: int
    product_summary: str


@dataclass(frozen=True)
class Overview:
    """The numbers of /admin overview, the newest guild named when Discord knows it."""

    shown: DiscordShows
    stored: KeikoStored
    newest_guild_name: Optional[str]


class ErasedRecords(TypedDict):
    """How many records erasing a guild removed, of each kind."""

    events: int
    months: int
    profile: int
    traces: int


async def send_log_file_from_channel_by_date(
    date: str, interaction: discord.Interaction[DiscordBot]
) -> None:
    """Answer with the link of the daily log of `date`, or say it was not found."""
    bot = interaction.client
    channel = bot.get_channel(bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)
    if not isinstance(channel, discord.abc.Messageable):
        raise LookupError(f"the logs files channel {channel!r} holds no messages")

    async for message in channel.history(limit=None):
        if date not in message.content:
            continue

        daily_log = next(
            (
                file
                for file in message.attachments
                if logs_archive.is_daily_log(file.filename)
            ),
            None,
        )
        if daily_log is None:
            continue

        await interaction.followup.send(
            f":page_facing_up: Here is my log file for **{date}**! {daily_log.url}",
        )
        return

    await interaction.followup.send(f":pensive: Log file not found for **{date}**")


async def update_admin_configs(bot: DiscordBot, configs: Dict[str, int]) -> None:
    """Save operator ids and use them at once."""
    await data().keiko_settings.update_operator(configs)
    for key, value in configs.items():
        setattr(bot.config, key.upper(), value)

    analytics.record(
        "keiko.settings_changed",
        props={"group": "operator", "changed_keys": sorted(configs)},
    )


async def get_admin_configs() -> Mapping[str, Any]:
    """The operator ids Keiko saved."""
    settings = await data().keiko_settings.find_keiko_settings()
    operator: Mapping[str, Any] = settings.get(DBConfigs.KEIKO_OPERATOR, {})
    return operator


async def get_overview_data(bot: DiscordBot) -> Overview:
    """The overview's numbers: Discord's cache read on its loop, and Keiko's records."""
    shown = _what_discord_shows(bot)
    stored = await _what_keiko_stored({str(guild.id) for guild in bot.guilds})

    newest_guild_id = stored.newest_guild_id
    newest_guild = bot.get_guild(int(newest_guild_id)) if newest_guild_id else None
    newest_guild_name = newest_guild.name if newest_guild else newest_guild_id
    return Overview(shown=shown, stored=stored, newest_guild_name=newest_guild_name)


def ready_since(bot: DiscordBot) -> datetime:
    """When Keiko got ready, or now while it has not yet."""
    return bot.ready_time or datetime.now()


def _what_discord_shows(bot: DiscordBot) -> DiscordShows:
    ready_time = ready_since(bot)
    uptime = datetime.now() - ready_time
    ready_time_utc = ready_time.replace(tzinfo=timezone.utc)
    last_restart = (
        ready_time_utc.strftime("%Y-%m-%d %H:%M")
        + f" ({format_relative_time(ready_time_utc)})"
    )

    guilds = list(bot.guilds)
    guilds_with_members = [guild for guild in guilds if guild.member_count]
    largest_guild = (
        max(guilds_with_members, key=lambda guild: guild.member_count or 0)
        if guilds_with_members
        else None
    )

    return DiscordShows(
        uptime=format_datetime_output(uptime),
        last_restart=last_restart,
        latency_ms=round(bot.latency * 1000),
        status=bot.status.name if bot.status else "unknown",
        activity_name=bot.activity.name if bot.activity else "N/A",
        total_channels=sum(
            len(guild.text_channels) + len(guild.voice_channels) for guild in guilds
        ),
        largest_guild_name=largest_guild.name if largest_guild else "N/A",
        largest_guild_members=(largest_guild.member_count or 0) if largest_guild else 0,
        memory_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
    )


async def servers_overview(since: datetime) -> Mapping[str, Any]:
    """Servers Keiko is in and left, its top owner, the newest and the count since."""
    pipeline: List[Dict[str, Any]] = [
        {
            "$facet": {
                "guild_status": [
                    {"$group": {"_id": "$state.present", "count": {"$sum": 1}}}
                ],
                "top_owner": [
                    {"$match": {"state.present": True}},
                    {"$group": {"_id": "$state.owner_id", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                    {"$limit": 1},
                ],
                "newest_guild": [
                    {"$match": {"state.present": True}},
                    {"$sort": {"created_at": -1}},
                    {"$limit": 1},
                    {"$project": {"guild_id": "$_id", "created_at": 1}},
                ],
                "monthly_growth": [
                    {"$match": {"created_at": {"$gte": since}}},
                    {"$count": "total"},
                ],
            }
        }
    ]
    found = await data().guild_settings.aggregate_settings(pipeline)
    return found[0] if found else {}


async def _what_keiko_stored(guild_ids: Set[str]) -> KeikoStored:
    first_day_of_month = datetime.now(tz=timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    facet = await servers_overview(first_day_of_month)

    active_guilds = next(
        (
            item["count"]
            for item in facet.get("guild_status", [])
            if item["_id"] is True
        ),
        0,
    )

    top_owner = facet.get("top_owner", [])
    top_owner_id = top_owner[0]["_id"] if top_owner else None
    top_owner_count = top_owner[0]["count"] if top_owner else 0

    feature_adoption = await count_feature_adoption(guild_ids)

    newest_guild = facet.get("newest_guild", [])
    newest_guild_id = newest_guild[0].get("guild_id") if newest_guild else None
    newest_guild_created = newest_guild[0].get("created_at") if newest_guild else None

    monthly_growth_data = facet.get("monthly_growth", [])
    monthly_growth = monthly_growth_data[0]["total"] if monthly_growth_data else 0

    since = datetime.now(timezone.utc) - timedelta(
        days=Commands.ANALYTICS_OVERVIEW_DAYS
    )
    command_calls, command_errors = await analytics_reports.command_usage(since)
    total_calls = sum(command_calls.values())
    total_errors = sum(command_errors.values())
    error_rate = round(total_errors / total_calls * 100, 1) if total_calls > 0 else None

    return KeikoStored(
        active_guilds=active_guilds,
        top_owner_id=top_owner_id,
        top_owner_count=top_owner_count,
        feature_adoption=feature_adoption,
        newest_guild_id=newest_guild_id,
        newest_guild_created=newest_guild_created,
        monthly_growth=monthly_growth,
        command_calls=command_calls,
        command_errors=command_errors,
        total_calls=total_calls,
        error_rate=error_rate,
        redis_keys=await to_thread(cache.count_keys),
        twitch_streamers=await data().keiko_subscriptions.count_subscriptions("twitch"),
        product_summary=await build_product_summary(),
    )


async def count_feature_adoption(guild_ids: Set[str]) -> Dict[str, int]:
    """How many of these servers have each feature on, by the record the bot runs on."""
    cogs = data().guild_cogs
    return {
        key: sum(
            1
            for guild_id in await cogs.find_enabled_guild_ids(key)
            if guild_id in guild_ids
        )
        for key in feature_keys()
    }


def build_overview_embed(bot: DiscordBot, overview: Overview) -> discord.Embed:
    """The /admin overview embed, three rows of numbers and then the long fields."""
    shown, stored = overview.shown, overview.stored
    embed = discord.Embed(
        title=":bar_chart: Keiko Overview",
        description=(
            f"Online for **{shown.uptime}** | Last restart: {shown.last_restart}"
        ),
        color=int(Style.BACKGROUND_COLOR, 16),
    )
    embed.set_thumbnail(url=KeikoIcons.IMAGE_02)

    embed.add_field(
        name=":heartpulse: Latency",
        value=f"**{shown.latency_ms}**ms",
        inline=True,
    )
    embed.add_field(
        name=":satellite: Status",
        value=f"{shown.status} — {shown.activity_name}",
        inline=True,
    )
    embed.add_field(
        name=":desktop: Resources",
        value=f"RAM: **{shown.memory_mb} MB**",
        inline=True,
    )

    embed.add_field(
        name=":homes: Guilds",
        value=f"**{stored.active_guilds}** active",
        inline=True,
    )

    if stored.top_owner_id:
        top_owner_value = (
            f"<@{stored.top_owner_id}> (**{stored.top_owner_count}** guilds)"
        )
    else:
        top_owner_value = "N/A"
    embed.add_field(name=":crown: Top Owner", value=top_owner_value, inline=True)

    created = stored.newest_guild_created
    if overview.newest_guild_name and created:
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        newest_value = f"{overview.newest_guild_name} ({format_relative_time(created)})"
    else:
        newest_value = "N/A"
    embed.add_field(name=":new: Newest Guild", value=newest_value, inline=True)

    embed.add_field(
        name=":trophy: Largest Guild",
        value=(
            f"{shown.largest_guild_name} (**{shown.largest_guild_members:,}** members)"
        ),
        inline=True,
    )
    embed.add_field(
        name=":calendar: Growth",
        value=f"**{stored.monthly_growth}** guilds this month",
        inline=True,
    )

    embed.add_field(
        name=":tools: Feature Adoption",
        value="\n".join(_adoption_lines(stored.feature_adoption)),
        inline=False,
    )

    error_rate = f"**{stored.error_rate}%**" if stored.error_rate is not None else "N/A"
    embed.add_field(
        name=(
            f":joystick: Command Usage, last {Commands.ANALYTICS_OVERVIEW_DAYS} days "
            f"(error rate: {error_rate})"
        ),
        value="\n".join(_usage_lines(stored)),
        inline=False,
    )

    integrations_value = (
        f"Twitch subs: **{stored.twitch_streamers}** unique streamers\n"
        f"Redis keys: **{stored.redis_keys}**\n"
        f"Total channels: **{shown.total_channels:,}**"
    )
    embed.add_field(
        name=":satellite_orbital: Integrations",
        value=integrations_value,
        inline=False,
    )

    embed.add_field(
        name=":chart_with_upwards_trend: Product",
        value=stored.product_summary,
        inline=False,
    )

    embed.set_footer(text=f"• Keiko {bot.config.ENVIRONMENT}")

    return embed


def _adoption_lines(adoption: Dict[str, int]) -> List[str]:
    lines = []
    for command in feature_keys():
        count = adoption.get(command, 0)
        icon = "✅" if count > 0 else "❌"
        label = "guild" if count == 1 else "guilds"
        lines.append(f"{icon} {command}: **{count}** {label}")
    return lines


def _usage_lines(stored: KeikoStored) -> List[str]:
    calls, errors_by_command = stored.command_calls, stored.command_errors
    lines = []
    for command in sorted(calls, key=lambda name: calls[name], reverse=True):
        count = calls[command]
        errors = errors_by_command.get(command, 0)
        rate = round(errors / count * 100, 1) if count > 0 else 0
        error_info = f" | **{errors}** errors ({rate}%)" if errors > 0 else ""
        lines.append(f"{command}: **{count:,}** calls{error_info}")

    lines.append(f"Total: **{stored.total_calls:,}** calls")
    return lines


async def build_product_summary() -> str:
    """Configured versus actually working, the distinction command counts hide."""
    lifetimes = await data().analytics_lifetime.find_lifetimes()
    live = [profile for profile in lifetimes if not profile.get("removed_at")]
    activated = [profile for profile in live if profile.get("last_value_at")]
    abandoned = await analytics_reports.abandoned_features(lifetimes=lifetimes)
    at_risk = await analytics_reports.guilds_at_risk()

    return (
        f"Guilds delivering value: **{len(activated)}** of **{len(live)}**\n"
        f"Features configured but never used: **{len(abandoned)}**\n"
        f"Guilds quiet for 14+ days: **{len(at_risk)}**"
    )


async def forget_guild(guild_id: str) -> ErasedRecords:
    """Erase every record Keiko keeps of a guild's activity, the queued ones first."""
    await analytics.flush()
    await analytics.written()
    store = data()
    current = ("changes", "activity", "interactions")
    found = await store.databases.collection_names("events")
    audits = [name for name in found if name not in current]

    events = sum(
        [
            await collection.delete_by_guild(guild_id)
            for collection in (
                store.events_changes,
                store.events_activity,
                store.events_interactions,
            )
        ]
    )
    events += sum(
        [
            await store.databases.delete_guild_rows("events", name, guild_id)
            for name in audits
        ]
    )
    events += sum(
        [
            await store.databases.delete_guild_rows("guild", name, guild_id)
            for name in ("analytics_events", "blocked_links")
        ]
    )
    months = await store.databases.delete_guild_rows(
        "guild", "analytics_guild_month", guild_id
    )
    profile = await store.databases.delete_guild_rows(
        "guild", "analytics_guild_profile", guild_id, "_id"
    )
    return {
        "events": events,
        "months": months + await store.analytics_counts.delete_by_guild(guild_id),
        "profile": profile + await store.analytics_lifetime.delete_by_guild(guild_id),
        "traces": await store.traces_runs.delete_by_guild(guild_id),
    }

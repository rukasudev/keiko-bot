"""The weekly analytics digest Keiko posts on its own, what needs a decision first."""

from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import discord
from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core import analytics_reports
from app.core.analytics_writer import metric_key
from app.core.utils import describe_default, describe_step
from app.data import data
from app.data.collection import Document
from app.forms.features import feature_keys
from app.ui.embed import base_embed

VALUE_LABELS = {
    "block_links": "links blocked",
    "welcome_messages": "welcomes sent",
    "default_roles": "roles assigned",
    "notifications_twitch": "live notifications",
    "notifications_youtube_video": "video notifications",
    "reminders_birthday": "birthdays celebrated",
    "stream_elements_commands": "commands answered",
}


async def build_weekly_digest(
    days: int = constants.ANALYTICS_DIGEST_WINDOW_DAYS,
    guild_count: Optional[int] = None,
) -> discord.Embed:
    """The digest of the last `days`, over the `guild_count` guilds the bot knows."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    keiko_days = await _keiko_days(since)
    servers = await _server_counts(since)

    attention = await _attention_lines(since, servers)
    delivered = _delivered_lines(since, keiko_days)
    unused = await _unused_lines()
    slowest = await _slow_step_lines()

    description = [await _headline(since, keiko_days, servers, guild_count)]

    if attention:
        description += ["", "**⚠️ Needs attention**"] + attention
    if unused:
        description += ["", "**📉 Settings nobody uses**"] + unused
    if slowest:
        description += ["", f"**⏱️ Slowest steps** ({_raw_window()})"] + slowest
    if delivered:
        description += ["", "**✅ Delivered value**"] + delivered

    if not attention:
        description += ["", "Nothing needs a decision this week."]

    first = since + timedelta(days=1)
    window = (
        f"{first.strftime('%d/%m')} – {datetime.now(timezone.utc).strftime('%d/%m')}"
    )
    return base_embed(
        f"📊 Keiko — week of {window}",
        "\n".join(description),
        footer="run `/admin insights` to dig into any of these",
    )


def _raw_window() -> str:
    return f"last {constants.EVENTS_INTERACTIONS_TTL_SECONDS // 86400} days"


async def _keiko_days(since: datetime) -> List[Document]:
    return await data().analytics_keiko.find_counters(
        [f"day:{month}" for month in analytics_reports.window_months(since)]
    )


async def _server_counts(since: datetime) -> List[Document]:
    found: List[Document] = []
    for month in analytics_reports.window_months(since):
        found += await data().analytics_counts.find_by_month(month)
    return found


async def _headline(
    since: datetime,
    keiko_days: List[Document],
    servers: List[Document],
    guild_count: Optional[int] = None,
) -> str:
    lifetimes = await data().analytics_lifetime.find_lifetimes()
    live = [profile for profile in lifetimes if not profile.get("removed_at")]

    arrivals = analytics_reports.window_totals(
        keiko_days,
        since,
        {
            metric_key("guild.joined", None): "joined",
            metric_key("guild.removed", None): "removed",
        },
    )
    joined, removed = arrivals.get("joined", 0), arrivals.get("removed", 0)

    line = (
        f"**{guild_count}** guilds"
        if guild_count is not None
        else f"**{len(live)}** guilds seen"
    )
    if joined or removed:
        line += f" ({_signed(joined)} joined, {_signed(-removed)} left)"
    delivering = _guilds_with(servers, since, analytics_reports.value_metrics())
    line += f" · **{len(delivering)}** delivered value this week"
    return line


def _guilds_with(
    servers: List[Document], since: datetime, wanted: Dict[str, str]
) -> Set[Optional[str]]:
    return {
        document.get("guild_id")
        for document in servers
        if analytics_reports.window_totals([document], since, wanted)
    }


def _signed(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _where(row: Dict[str, Any]) -> str:
    if not row["step_key"]:
        return "before the first step"
    return f"at `{describe_step(row['feature'], row['step_key'])}`"


async def _attention_lines(since: datetime, servers: List[Document]) -> List[str]:
    lines = []

    for row in (await analytics_reports.dropoff())[:3]:
        reasons = sorted(row["reasons"].items(), key=lambda item: item[1], reverse=True)
        top_reason = reasons[0][0] if reasons else "unknown"
        setups = "setup" if row["lost"] == 1 else "setups"
        lines.append(
            f"• **{row['feature']}**: {row['lost']} {setups} died "
            f"{_where(row)} ({top_reason.replace('-', ' ')}, {_raw_window()})"
        )

    blocked = _guilds_with(servers, since, _refusal_metrics())
    if blocked:
        lines.append(
            f"• **{len(blocked)}** guilds where Discord refused an action "
            f"(the bot cannot do its job there)"
        )

    dead = [
        row
        for row in await analytics_reports.abandoned_features()
        if row["days_since_setup"] <= 30
    ]
    if dead:
        lines.append(
            f"• **{len(dead)}** features configured in the last 30 days that "
            f"never delivered anything"
        )

    return lines


def _refusal_metrics() -> Dict[str, str]:
    return {
        metric_key("value.blocked_by_permission", feature): feature
        for feature in feature_keys()
    }


async def _unused_lines() -> List[str]:
    lines = []
    seen = set()
    for row in await analytics_reports.unused_settings():
        if row["feature"] in seen:
            continue
        seen.add(row["feature"])
        lines.append(
            f"• **{row['feature']}** / `{row['key']}` — **{row['filled']}** of "
            f"**{row['total']}** guilds{describe_default(row)}"
        )
        if len(lines) == 3:
            break
    return lines


async def _slow_step_lines() -> List[str]:
    lines = []
    for row in (await analytics_reports.step_durations())[:3]:
        seconds = row["median_ms"] / 1000
        readable = f"{seconds:.0f}s" if seconds < 60 else f"{seconds / 60:.1f}min"
        lines.append(
            f"• **{row['feature']}** / `{row['step_key']}` — median {readable}"
        )
    return lines


def _delivered_lines(since: datetime, keiko_days: List[Document]) -> List[str]:
    totals = analytics_reports.value_counts(keiko_days, since)
    if not totals:
        return []

    parts = [
        f"**{count}** {VALUE_LABELS.get(feature, feature)}"
        for feature, count in sorted(
            totals.items(), key=lambda item: item[1], reverse=True
        )
    ]
    return ["• " + " · ".join(parts)]


class WeeklyDigest(commands.Cog):
    """Posts the weekly digest on the admin logs channel, on Mondays in UTC."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Start the weekly loop once the bot adds the cog."""
        self.send_weekly_digest.start()

    async def cog_unload(self) -> None:
        """Stop the weekly loop."""
        self.send_weekly_digest.cancel()

    @tasks.loop(time=time(hour=constants.ANALYTICS_DIGEST_HOUR))
    async def send_weekly_digest(self) -> None:
        """Fires daily at its UTC hour and acts on Mondays in UTC."""
        if datetime.now(timezone.utc).weekday() != constants.ANALYTICS_DIGEST_WEEKDAY:
            return

        channel: Any = self.bot.get_channel(self.bot.config.ADMIN_LOGS_CHANNEL_ID)
        if not channel:
            return

        try:
            digest = await build_weekly_digest(guild_count=len(self.bot.guilds))
            await channel.send(embed=digest)
        except Exception as error:
            logger.warn(
                f"Weekly digest failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @send_weekly_digest.before_loop
    async def before_digest(self) -> None:
        """Wait until Keiko is ready."""
        await self.bot.wait_until_ready()


async def setup(bot: DiscordBot) -> None:
    """Load the weekly digest cog."""
    await bot.add_cog(WeeklyDigest(bot))

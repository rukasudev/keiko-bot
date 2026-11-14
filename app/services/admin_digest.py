"""The weekly message that arrives on its own.

Every other analytics surface waits for someone to remember it exists. This one
does not, which is why it leads with what needs a decision and keeps the rest
short. A quiet week still gets a message: a digest that sometimes fails to
arrive makes you doubt it every week. Reference: docs/analytics.md
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import discord

from app.components.embed import base_embed
from app.constants import Commands as constants
from app.core import analytics_reports
from app.core.analytics_sink import metric_key
from app.data import data
from app.core.utils import describe_default, describe_step

VALUE_LABELS = {
    constants.BLOCK_LINKS_KEY: "links blocked",
    constants.WELCOME_MESSAGES_KEY: "welcomes sent",
    constants.DEFAULT_ROLES_KEY: "roles assigned",
    constants.NOTIFICATIONS_TWITCH_KEY: "live notifications",
    constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: "video notifications",
    constants.REMINDERS_BIRTHDAY_KEY: "birthdays celebrated",
    constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY: "commands answered",
}


async def build_weekly_digest(
    days: int = constants.ANALYTICS_DIGEST_WINDOW_DAYS,
    guild_count: Optional[int] = None,
) -> discord.Embed:
    """`guild_count` is how many guilds the bot is in, which only the bot knows.

    Counting analytics profiles instead reports how many guilds did something
    since analytics was deployed — on the first week that was 12 out of 73.
    """
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
    window = f"{first.strftime('%d/%m')} – {datetime.now(timezone.utc).strftime('%d/%m')}"
    return base_embed(
        f"📊 Keiko — week of {window}",
        "\n".join(description),
        footer="run `/admin insights` to dig into any of these",
    )


def _raw_window() -> str:
    """The reports rebuilt from raw events cover their whole retention, not the week."""
    return f"last {constants.EVENTS_INTERACTIONS_TTL_SECONDS // 86400} days"


async def _keiko_days(since: datetime) -> List[Dict[str, Any]]:
    """Keiko's own daily counts, every server summed, for the months the week touches."""
    return await data().analytics_keiko.find_counters(
        [f"day:{month}" for month in analytics_reports.window_months(since)]
    )


async def _server_counts(since: datetime) -> List[Dict[str, Any]]:
    """Every server's counts for the months the week touches, read once."""
    found: List[Dict[str, Any]] = []
    for month in analytics_reports.window_months(since):
        found += await data().analytics_counts.find_by_month(month)
    return found


async def _headline(
    since: datetime,
    keiko_days: List[Dict[str, Any]],
    servers: List[Dict[str, Any]],
    guild_count: Optional[int] = None,
) -> str:
    lifetimes = await data().analytics_lifetime.find_lifetimes()
    live = [profile for profile in lifetimes if not profile.get("removed_at")]

    arrivals = analytics_reports.window_totals(
        keiko_days,
        since,
        {metric_key("guild.joined", None): "joined", metric_key("guild.removed", None): "removed"},
    )
    joined, removed = arrivals.get("joined", 0), arrivals.get("removed", 0)

    # Two different numbers, and never the same label: a caller that cannot
    # supply the real count must not publish the smaller one as if it were.
    line = (
        f"**{guild_count}** guilds" if guild_count is not None
        else f"**{len(live)}** guilds seen"
    )
    if joined or removed:
        line += f" ({_signed(joined)} joined, {_signed(-removed)} left)"
    # In the window, not ever: a profile keeps `last_value_at` for as long as it
    # exists, so counting profiles turns "this week" into "at some point".
    delivering = _guilds_with(servers, since, analytics_reports.value_metrics())
    line += f" · **{len(delivering)}** delivered value this week"
    return line


def _guilds_with(
    servers: List[Dict[str, Any]], since: datetime, wanted: Dict[str, str]
) -> Set[str]:
    """The servers that counted any of the metrics `wanted` names inside the window."""
    return {
        document.get("guild_id")
        for document in servers
        if analytics_reports.window_totals([document], since, wanted)
    }


def _signed(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _where(row: Dict[str, Any]) -> str:
    """A session that reached no step has no step to name — `None` is not one.

    It is also the most interesting drop-off there is: whoever opened the setup
    closed it on the opening screen, so the copy on that screen is the suspect.
    """
    if not row["step_key"]:
        return "before the first step"
    return f"at `{describe_step(row['feature'], row['step_key'])}`"


async def _attention_lines(since: datetime, servers: List[Dict[str, Any]]) -> List[str]:
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
        row for row in await analytics_reports.abandoned_features()
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
        for feature in constants.COMMANDS_LIST
    }


async def _unused_lines() -> List[str]:
    """One per feature, so a single noisy form does not fill the whole block."""
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


def _delivered_lines(since: datetime, keiko_days: List[Dict[str, Any]]) -> List[str]:
    totals = analytics_reports.value_counts(keiko_days, since)
    if not totals:
        return []

    parts = [
        f"**{count}** {VALUE_LABELS.get(feature, feature)}"
        for feature, count in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]
    return ["• " + " · ".join(parts)]

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
from app.data import analytics as analytics_data
from app.services import analytics_reports
from app.services.analytics_sink import metric_key
from app.services.utils import describe_default, describe_step

VALUE_LABELS = {
    constants.BLOCK_LINKS_KEY: "links blocked",
    constants.WELCOME_MESSAGES_KEY: "welcomes sent",
    constants.DEFAULT_ROLES_KEY: "roles assigned",
    constants.NOTIFICATIONS_TWITCH_KEY: "live notifications",
    constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: "video notifications",
    constants.REMINDERS_BIRTHDAY_KEY: "birthdays celebrated",
    constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY: "commands answered",
}


def build_weekly_digest(
    days: int = constants.ANALYTICS_DIGEST_WINDOW_DAYS,
    guild_count: Optional[int] = None,
) -> discord.Embed:
    """`guild_count` is how many guilds the bot is in, which only the bot knows.

    Counting analytics profiles instead reports how many guilds did something
    since analytics was deployed — on the first week that was 12 out of 73.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    attention = _attention_lines(since)
    delivered = _delivered_lines(since)
    unused = _unused_lines()
    slowest = _slow_step_lines()

    description = [_headline(since, guild_count)]

    if attention:
        description += ["", "**⚠️ Needs attention**"] + attention
    if unused:
        description += ["", "**📉 Settings nobody uses**"] + unused
    if slowest:
        description += ["", "**⏱️ Slowest steps**"] + slowest
    if delivered:
        description += ["", "**✅ Delivered value**"] + delivered

    if not attention:
        description += ["", "Nothing needs a decision this week."]

    window = f"{since.strftime('%d/%m')} – {datetime.now(timezone.utc).strftime('%d/%m')}"
    return base_embed(
        f"📊 Keiko — week of {window}",
        "\n".join(description),
        footer="run `/admin insights` to dig into any of these",
    )


def _headline(since: datetime, guild_count: Optional[int] = None) -> str:
    profiles = analytics_data.find_profiles()
    live = [profile for profile in profiles if not profile.get("removed_at")]

    joined = len(_events_since("guild.joined", since))
    removed = len(_events_since("guild.removed", since))

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
    line += f" · **{len(_delivering_guilds(since))}** delivered value this week"
    return line


def _delivering_guilds(since: datetime) -> Set[str]:
    """Guilds with at least one delivery inside the window."""
    wanted = _value_metrics()
    days = _window_days(since)

    guilds = set()
    for bucket in _all_month_buckets():
        month = bucket.get("month")
        for day, metrics in (bucket.get("days") or {}).items():
            if f"{month}|{day}" in days and any(metric in wanted for metric in metrics):
                guilds.add(bucket.get("guild_id"))
    return guilds


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


def _attention_lines(since: datetime) -> List[str]:
    lines = []

    for row in analytics_reports.dropoff()[:3]:
        reasons = sorted(row["reasons"].items(), key=lambda item: item[1], reverse=True)
        top_reason = reasons[0][0] if reasons else "unknown"
        setups = "setup" if row["lost"] == 1 else "setups"
        lines.append(
            f"• **{row['feature']}**: {row['lost']} {setups} died "
            f"{_where(row)} ({top_reason.replace('-', ' ')})"
        )

    blocked = _guilds_with_permission_failures(since)
    if blocked:
        lines.append(
            f"• **{len(blocked)}** guilds where Discord refused an action "
            f"(the bot cannot do its job there)"
        )

    dead = [
        row for row in analytics_reports.abandoned_features()
        if row["days_since_setup"] <= 30
    ]
    if dead:
        lines.append(
            f"• **{len(dead)}** features configured in the last 30 days that "
            f"never delivered anything"
        )

    return lines


def _guilds_with_permission_failures(since: datetime) -> List[str]:
    return list({
        event.get("guild_id")
        for event in _events_since("value.blocked_by_permission", since)
        if event.get("guild_id")
    })


def _unused_lines() -> List[str]:
    """One per feature, so a single noisy form does not fill the whole block."""
    lines = []
    seen = set()
    for row in analytics_reports.unused_settings():
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


def _slow_step_lines() -> List[str]:
    lines = []
    for row in analytics_reports.step_durations()[:3]:
        seconds = row["median_ms"] / 1000
        readable = f"{seconds:.0f}s" if seconds < 60 else f"{seconds / 60:.1f}min"
        lines.append(
            f"• **{row['feature']}** / `{row['step_key']}` — median {readable}"
        )
    return lines


def _delivered_lines(since: datetime) -> List[str]:
    totals = _value_totals(since)
    if not totals:
        return []

    parts = [
        f"**{count}** {VALUE_LABELS.get(feature, feature)}"
        for feature, count in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]
    return ["• " + " · ".join(parts)]


def _value_metrics() -> Dict[str, str]:
    """Every counter that means "the feature did its job", mapped to its feature."""
    return {
        metric_key(event, feature): feature
        for feature in constants.COMMANDS_LIST
        for event in ("value.delivered", "feature.action_performed")
    }


def _window_days(since: datetime) -> Set[str]:
    return {
        (since + timedelta(days=offset)).strftime("%Y-%m|%d")
        for offset in range((datetime.now(timezone.utc) - since).days + 1)
    }


def _value_totals(since: datetime) -> Dict[str, int]:
    """Deliveries in the window, summed from the monthly buckets."""
    wanted = _value_metrics()
    days = _window_days(since)

    totals: Dict[str, int] = {}
    for bucket in _all_month_buckets():
        month = bucket.get("month")
        for day, metrics in (bucket.get("days") or {}).items():
            if f"{month}|{day}" not in days:
                continue
            for metric, count in metrics.items():
                feature = wanted.get(metric)
                if feature:
                    totals[feature] = totals.get(feature, 0) + count
    return totals


def _all_month_buckets() -> List[Dict[str, Any]]:
    months = {
        datetime.now(timezone.utc).strftime("%Y-%m"),
        (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m"),
    }
    buckets = []
    for month in months:
        buckets += analytics_data.find_month_buckets_by_month(month)
    return buckets


def _events_since(event: str, since: datetime) -> List[Dict[str, Any]]:
    return [
        record for record in analytics_data.find_events({"event": event})
        if _at_or_after(record.get("ts"), since)
    ]


def _at_or_after(moment: Any, since: datetime) -> bool:
    if not isinstance(moment, datetime):
        return False
    reference = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return reference >= since

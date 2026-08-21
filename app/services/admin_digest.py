"""The weekly message that arrives on its own.

Every other analytics surface waits for someone to remember it exists. This one
does not, which is why it leads with what needs a decision and keeps the rest
short. A quiet week still gets a message: a digest that sometimes fails to
arrive makes you doubt it every week. Reference: docs/analytics.md
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import discord

from app.components.embed import base_embed
from app.constants import Commands as constants
from app.data import analytics as analytics_data
from app.services import analytics_reports
from app.services.analytics_sink import metric_key

VALUE_LABELS = {
    constants.BLOCK_LINKS_KEY: "links blocked",
    constants.WELCOME_MESSAGES_KEY: "welcomes sent",
    constants.DEFAULT_ROLES_KEY: "roles assigned",
    constants.NOTIFICATIONS_TWITCH_KEY: "live notifications",
    constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: "video notifications",
    constants.REMINDERS_BIRTHDAY_KEY: "birthdays celebrated",
    constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY: "commands answered",
}


def build_weekly_digest(days: int = constants.ANALYTICS_DIGEST_WINDOW_DAYS) -> discord.Embed:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    attention = _attention_lines(since)
    delivered = _delivered_lines(since)
    unused = _unused_lines()
    slowest = _slow_step_lines()

    description = [_headline(since)]

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


def _headline(since: datetime) -> str:
    profiles = analytics_data.find_profiles()
    live = [profile for profile in profiles if not profile.get("removed_at")]
    delivering = [profile for profile in live if profile.get("last_value_at")]

    joined = len(_events_since("guild.joined", since))
    removed = len(_events_since("guild.removed", since))

    line = f"**{len(live)}** guilds"
    if joined or removed:
        line += f" ({_signed(joined)} joined, {_signed(-removed)} left)"
    line += f" · **{len(delivering)}** delivering value"
    return line


def _signed(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _attention_lines(since: datetime) -> List[str]:
    lines = []

    for row in analytics_reports.dropoff()[:3]:
        reasons = sorted(row["reasons"].items(), key=lambda item: item[1], reverse=True)
        top_reason = reasons[0][0] if reasons else "unknown"
        setups = "setup" if row["lost"] == 1 else "setups"
        lines.append(
            f"• **{row['feature']}**: {row['lost']} {setups} died at "
            f"`{row['step_key']}` ({top_reason.replace('-', ' ')})"
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
            f"**{row['total']}** guilds"
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


def _value_totals(since: datetime) -> Dict[str, int]:
    """Deliveries in the window, summed from the monthly buckets."""
    wanted = {
        metric_key(event, feature): feature
        for feature in constants.COMMANDS_LIST
        for event in ("value.delivered", "feature.action_performed")
    }
    days = {
        (since + timedelta(days=offset)).strftime("%Y-%m|%d")
        for offset in range((datetime.now(timezone.utc) - since).days + 1)
    }

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

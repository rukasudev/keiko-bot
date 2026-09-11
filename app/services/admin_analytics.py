"""Screens for the /admin analytics commands.

Admin-only surfaces, restricted to the admin guild, so they follow the existing
English-only convention of the admin cog rather than the localized product copy
rules. Every number is read from the guild profile or the monthly buckets, and
every comparison prints the population it was computed from — with 80 guilds a
percentage without its N is noise. Reference: docs/analytics.md
"""
from typing import Any, Dict, List, Optional

import discord

from app.components.embed import base_embed
from app.constants import Commands as constants
from app.services import analytics, analytics_reports
from app.services.utils import describe_default, describe_step

EVENT_ICONS = {
    "guild.joined": "➡️",
    "guild.greeting_sent": "👋",
    "feature.setup_opened": "🔵",
    "feature.manager_opened": "🛠️",
    "setup.step_viewed": "▫️",
    "setup.validation_failed": "⚠️",
    "setup.required_missing": "⚠️",
    "setup.discarded": "🚫",
    "setup.discard_recovered": "↩️",
    "setup.completed": "✅",
    "config.changed": "🔧",
    "feature.paused": "⏸️",
    "feature.unpaused": "▶️",
    "feature.disabled": "🛑",
    "value.blocked_by_permission": "🚷",
    "feature.tested": "👁️",
    "command.failed": "❌",
    "guild.removed": "🚪",
}


def build_funnel_embed(feature: str) -> discord.Embed:
    steps = analytics_reports.funnel(feature)
    opened = steps["setup_opened"]

    lines = [
        _funnel_line("Setup opened", steps["setup_opened"], opened),
        _funnel_line("Setup completed", steps["setup_completed"], opened),
        _funnel_line("Delivered value", steps["activated"], opened),
        _funnel_line("Value on a 2nd day", steps["recurring"], opened),
        "",
        f"Discarded explicitly: **{steps['setup_discarded']}**",
    ]

    return base_embed(
        f"📉 Funnel — {feature}",
        "\n".join(lines),
        footer="percentages are of setups opened; N is absolute",
    )


def _funnel_line(label: str, value: int, total: int) -> str:
    share = f" ({round(100 * value / total)}%)" if total else ""
    return f"**{label}** — {value}{share}"


def build_friction_embed(feature: Optional[str] = None) -> discord.Embed:
    rows = analytics_reports.friction(feature)[:10]
    if not rows:
        return base_embed("🧱 Friction", "No validation failures recorded yet.")

    lines = [
        f"`{row['failures']}x` **{row['feature']}** / "
        f"`{describe_step(row['feature'], row['step_key'])}` — "
        f"`{row['error_key']}` ({row['failures_per_session']} per session)"
        for row in rows
    ]
    return base_embed(
        "🧱 Friction",
        "\n".join(lines),
        footer="steps ranked by how often they reject a user",
    )


DROPOFF_REASONS = {
    "repeated-validation-failure": "tried the same field again and again",
    "validation-failure": "a value was rejected",
    "required-field-missing": "confirmed with a required field empty",
    "navigated-back-repeatedly": "went back more than once",
    "discarded-without-error": "discarded it with nothing failing",
    "left-without-a-recorded-problem": "left, nothing failed",
}


def build_dropoff_embed(feature: Optional[str] = None) -> discord.Embed:
    rows = analytics_reports.dropoff(feature)
    if not rows:
        return base_embed(
            "🛑 Where setups die",
            "No abandoned attempt recorded — every session either finished or "
            "is still open.",
        )

    lines = []
    for row in rows[:10]:
        reasons = sorted(row["reasons"].items(), key=lambda item: item[1], reverse=True)
        top = ", ".join(
            f"{DROPOFF_REASONS.get(reason, reason)} ({count})"
            for reason, count in reasons[:2]
        )
        step = describe_step(row["feature"], row["step_key"])
        lines.append(
            f"`{row['lost']}x` **{row['feature']}** / `{step}` — "
            f"{row['discarded']} discarded, {row['expired']} expired\n"
            f"⤷ {top}"
        )

    return base_embed(
        "🛑 Where setups die",
        "\n".join(lines),
        footer="expired = the view timed out, so the attempt can never finish",
    )


def build_session_embed(session: dict) -> discord.Embed:
    outcome_icons = {
        "completed": "✅", "discarded": "🚫", "expired": "⌛", "open": "⏳",
    }
    icon = outcome_icons.get(session["outcome"], "•")
    lines = [
        f"{icon} **{session['outcome']}** at step "
        f"`{describe_step(session['feature'], session['last_step'])}`",
        f"Guild `{session['guild_id']}` · <@{session['user_id']}> · via `{session['source']}`",
        "",
        f"Validation failures: **{session['validation_failures']}**",
        f"Required fields missed: **{session['required_misses']}**",
        f"Went back: **{session['back_count']}**",
    ]
    if session["failed_keys"]:
        lines.append(f"Rejected with: `{'`, `'.join(session['failed_keys'][:5])}`")
    if session["reason"]:
        lines.append("")
        lines.append(f"Reading: {DROPOFF_REASONS.get(session['reason'], session['reason'])}")

    return base_embed(
        f"🧭 Session {session['session_id']} — {session['feature']}",
        "\n".join(lines),
    )


def build_guild_journey_embed(guild_id: str) -> discord.Embed:
    profile = analytics_reports.guild_snapshot(guild_id)
    timeline = analytics_reports.guild_timeline(guild_id)

    header = (
        f"**{profile['days_in_guild']}** days · "
        f"**{profile['features_active']}** active of **{profile['features_configured']}** configured · "
        f"**{profile['value_events']}** deliveries"
    )
    if profile["days_since_last_value"] is not None:
        header += f" · last delivery **{profile['days_since_last_value']}d** ago"

    lines = [header, ""]
    for event in reversed(timeline):
        lines.append(_timeline_line(event))

    if not timeline:
        lines.append("No events recorded in the retention window.")

    return base_embed(
        f"🏠 Guild {guild_id}",
        "\n".join(lines),
        footer=f"detailed timeline covers the last {constants.ANALYTICS_EVENTS_TTL_SECONDS // 86400} days",
    )


def _timeline_line(event: Dict[str, Any]) -> str:
    icon = EVENT_ICONS.get(event["event"], "•")
    stamp = event["ts"].strftime("%d/%m %H:%M") if event.get("ts") else "--/--"
    detail = event.get("feature") or ""
    props = event.get("props") or {}

    step = describe_step(event.get("feature"), props.get("step_key")) if props.get("step_key") else None

    if event["event"] == "setup.validation_failed":
        detail = f"{detail} / {step} ({props.get('error_key')})"
    elif event["event"] == "setup.completed":
        detail = f"{detail} ({props.get('duration_bucket')}, {props.get('steps_viewed')} steps)"
    elif event["event"] == "setup.discarded":
        detail = f"{detail} at {step}"
    elif event["event"] == "guild.greeting_sent":
        detail = f"{props.get('features_offered')} features offered"
        if not props.get("channel_found"):
            detail += " — no writable channel"

    source = f" · via {event['source']}" if event.get("source") else ""
    return f"`{stamp}` {icon} {event['event']} — {detail}{source}"


def build_churn_embed() -> discord.Embed:
    comparison = analytics_reports.churn_comparison()
    removed, retained = comparison["removed"], comparison["retained"]

    lines = [
        _population_line("Removed", removed),
        _population_line("Retained", retained),
    ]

    if removed.get("count", 0) < 30 or retained.get("count", 0) < 30:
        lines.append("")
        lines.append("⚠️ Populations are too small to read as a conclusion.")

    at_risk = analytics_reports.guilds_at_risk()
    if at_risk:
        lines.append("")
        lines.append(f"**At risk** ({len(at_risk)} guilds quiet for 14+ days):")
        lines.extend(
            f"`{row['guild_id']}` — quiet **{row['days_quiet']}d**, "
            f"{row['value_events']} lifetime deliveries"
            for row in at_risk[:5]
        )

    return base_embed(
        "🚪 Churn",
        "\n".join(lines),
        footer="correlation, never cause — Discord does not report why a bot is removed",
    )


def _population_line(label: str, population: Dict[str, Any]) -> str:
    count = population.get("count", 0)
    if not count:
        return f"**{label}** — N=0"
    return (
        f"**{label}** — N={count} · "
        f"{population['pct_with_value']}% ever delivered value · "
        f"{population['avg_setups_completed']} setups on average"
    )


def build_abandoned_embed() -> discord.Embed:
    rows = analytics_reports.abandoned_features()[:15]
    if not rows:
        return base_embed(
            "🧊 Abandoned features",
            "Every configured feature has delivered value at least once.",
        )

    lines = [
        f"`{row['guild_id']}` — **{row['feature']}** configured "
        f"**{row['days_since_setup']}d** ago, never delivered"
        for row in rows
    ]
    return base_embed(
        "🧊 Abandoned features",
        "\n".join(lines),
        footer="configured and never delivered — the most actionable failure",
    )


def build_pipeline_embed() -> discord.Embed:
    stats = analytics.stats()
    lines = [
        f"Emitted: **{stats['emitted']}**",
        f"Flushed: **{stats['flushed']}**",
        f"Queue depth: **{stats['queue_depth']}**",
        f"Dropped: **{stats['dropped']}**",
        f"Unknown events: **{stats['unknown']}**",
        f"Undeclared props: **{stats['undeclared_props']}**",
        f"Enabled: **{analytics.is_enabled()}**",
    ]
    return base_embed("🩺 Analytics pipeline", "\n".join(lines))


def build_config_usage_embed(feature: Optional[str] = None) -> discord.Embed:
    """Settings nobody fills in — read from what is stored, so it needs no event."""
    rows = analytics_reports.unused_settings()
    if feature:
        rows = [row for row in rows if row["feature"] == feature]

    if not rows:
        return base_embed(
            "🧮 Settings nobody uses",
            "Every setting of every configured feature is used by someone.",
        )

    lines = [
        f"**{row['feature']}** / `{row['key']}` — **{row['filled']}** of "
        f"**{row['total']}** guilds ({row['share']}%){describe_default(row)}"
        for row in rows[:15]
    ]
    return base_embed(
        "🧮 Settings nobody uses",
        "\n".join(lines),
        footer="candidates for removal, a better default, or clearer copy",
    )


def build_step_timing_embed(feature: Optional[str] = None) -> discord.Embed:
    rows = analytics_reports.step_durations(feature)
    if not rows:
        return base_embed("⏱️ Slowest steps", "No step time recorded yet.")

    lines = [
        f"**{row['feature']}** / `{describe_step(row['feature'], row['step_key'])}` — median "
        f"**{_seconds(row['median_ms'])}** (n={row['samples']}, "
        f"slowest {_seconds(row['slowest_ms'])})"
        for row in rows[:10]
    ]

    distribution = analytics_reports.choice_distribution(feature)
    if distribution:
        lines.append("")
        lines.append("**What people picked**")
        for key, counts in list(distribution.items())[:5]:
            picks = ", ".join(f"`{value}` {count}" for value, count in counts.items())
            lines.append(f"{key} — {picks}")

    return base_embed(
        "⏱️ Slowest steps",
        "\n".join(lines),
        footer="time between interactions, not attention — a pause can be anything",
    )


def _seconds(milliseconds: int) -> str:
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f}min"


def insight_sections() -> List[Dict[str, Any]]:
    """The screens /admin insights offers, ordered by how often they matter."""
    return [
        {"key": "dropoff", "label": "Where setups die", "emoji": "🛑",
         "description": "the step people give up on, and what failed",
         "build": build_dropoff_embed},
        {"key": "friction", "label": "Friction", "emoji": "🧱",
         "description": "validations that reject people the most",
         "build": build_friction_embed},
        {"key": "abandoned", "label": "Abandoned features", "emoji": "🧊",
         "description": "configured and never delivered anything",
         "build": build_abandoned_embed},
        {"key": "config", "label": "Settings nobody uses", "emoji": "🧮",
         "description": "read from stored configuration, no events needed",
         "build": build_config_usage_embed},
        {"key": "timing", "label": "Slowest steps", "emoji": "⏱️",
         "description": "median time on each step, and what people picked",
         "build": build_step_timing_embed},
        {"key": "funnel", "label": "Funnel", "emoji": "📉",
         "description": "opened, completed, delivered, delivered again",
         "build": build_all_funnels_embed},
        {"key": "churn", "label": "Churn", "emoji": "🚪",
         "description": "removed versus retained, always with N",
         "build": build_churn_embed},
        {"key": "pipeline", "label": "Pipeline health", "emoji": "🩺",
         "description": "the analytics pipeline itself",
         "build": build_pipeline_embed},
    ]


def build_all_funnels_embed() -> discord.Embed:
    """Every feature side by side — one funnel per line instead of per command."""
    lines = []
    for feature in constants.COMMANDS_LIST:
        steps = analytics_reports.funnel(feature)
        if not steps["setup_opened"]:
            continue
        lines.append(
            f"**{feature}** — opened {steps['setup_opened']} · "
            f"completed {steps['setup_completed']} · "
            f"delivered {steps['activated']} · recurring {steps['recurring']}"
        )

    if not lines:
        return base_embed("📉 Funnel", "No setup has been opened yet.")

    return base_embed(
        "📉 Funnel",
        "\n".join(lines),
        footer="absolute counts; percentages hide how small the numbers still are",
    )


def feature_choices() -> List[str]:
    return list(constants.COMMANDS_LIST)

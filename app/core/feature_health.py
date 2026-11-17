"""How each feature did in its last days: what Keiko is missing for it, the deliveries
it refused or made, and the statistics embed every manager panel opens."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Sequence

import discord
from pydantic import Field

from app.ui.embed import base_embed, standard_footer
from app.constants import Commands as commands_constants
from app.core import analytics, i18n
from app.core.analytics_reports import window_months, window_totals
from app.core.analytics_writer import metric_key
from app.core.i18n import CopyBlock, CopyError, CopyText, Plural
from app.data import data
from app.core.commands import Command, Open, setup_feature
from app.core.utils import command_text, fill, ml, parse_locale, stored_value


class Stats(CopyBlock):
    """A feature's statistics: the embed its panel opens and its line on the recent
    activity, drawn by `send_feature_stats`, `health_line` and `figure_lines`."""

    title: CopyText = Field(
        description="The statistics embed's title, with one leading emoji.",
        examples=[{"en-us": "📊 Statistics", "pt-br": "📊 Estatísticas"}],
        json_schema_extra={"role": "copy"},
    )
    delivered: Plural = Field(
        description="The recent-activity line after the deliveries of the last days, "
        "by how many there were; $count, $days.",
        json_schema_extra={"role": "copy"},
    )
    empty: CopyText | None = Field(
        None,
        description="The only line `figure_lines` draws while the feature has no "
        "figure yet (the link blocker before its first block).",
        json_schema_extra={"role": "copy"},
    )
    figures: Open[CopyText] = Field(
        default_factory=dict,
        description="The label of each figure, keyed by the figure `figure_lines` "
        "draws as `emoji **label:** value`.",
        json_schema_extra={"role": "copy"},
    )


@dataclass(frozen=True)
class Figure:
    """One figure of a feature's statistics: the `command.stats.figures` label it
    reads, its emoji and its value."""

    name: str
    emoji: str
    value: Any


def _ids(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _ids_by_style(views: Any, style: str) -> List[str]:
    found: List[str] = []
    for view in views:
        if view.style == style:
            found.extend(_ids(view.machine_value))
        elif view.style == "composition":
            for item in view.value or []:
                for entry in item.values():
                    if entry.get("style") == style:
                        found.extend(_ids(stored_value(entry)))
    return list(dict.fromkeys(found))


def feature_problems(spec: Command, views: Any, guild: Any, locale: str) -> List[str]:
    """What Keiko is missing for one feature, one line each."""
    base = "texts.permissions"
    me = guild.me
    problems: List[str] = []

    def name(permission: str) -> str:
        return ml(f"texts.permission_names.{permission}", locale)

    for permission in spec.server_permissions:
        if not getattr(me.guild_permissions, permission, False):
            problems.append(
                fill(ml(f"{base}.server", locale), permission=name(permission))
            )

    channel_permissions = spec.channel_permissions
    if channel_permissions:
        for channel_id in _ids_by_style(views, "channel"):
            channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
            if channel is None:
                problems.append(ml(f"{base}.channel_gone", locale))
                continue
            granted = channel.permissions_for(me)
            for permission in channel_permissions:
                if not getattr(granted, permission, False):
                    problems.append(
                        fill(
                            ml(f"{base}.channel", locale),
                            permission=name(permission),
                            channel=f"<#{channel_id}>",
                        )
                    )

    if spec.assigns_roles:
        for role_id in _ids_by_style(views, "role"):
            role = guild.get_role(int(role_id)) if role_id.isdigit() else None
            if role is not None and role.position >= me.top_role.position:
                problems.append(
                    fill(ml(f"{base}.role_above", locale), role=role_id)
                )

    return problems


def _one_or_many(count: int) -> str:
    return "one" if count == 1 else "many"


def health_line(
    spec: Command, problems: List[str], refused: int, delivered: int, locale: str
) -> str:
    """One feature's last days in a sentence: a problem first, then the deliveries it
    refused, then the ones it made, else a quiet line."""
    base = "texts.recent"
    days = commands_constants.FEATURE_STATS_DAYS
    if problems:
        return problems[0]
    if refused:
        return fill(ml(f"{base}.refused_{_one_or_many(refused)}", locale), count=refused, days=days)
    if delivered:
        stats = i18n.block(f"{spec.key}.command.stats", Stats)
        line = stats.delivered.for_count(delivered).get(parse_locale(locale))
        return fill(line, count=delivered, days=days)
    return fill(ml(f"{base}.quiet", locale), days=days)


async def feature_health(
    guild: Any, guild_id: str, locale: str, spec: Command, views: Sequence[Any]
) -> str:
    """How one feature did in its last days over what it saved, the problem first."""
    key = spec.key
    since = datetime.now(timezone.utc) - timedelta(days=commands_constants.FEATURE_STATS_DAYS)
    months = await data().analytics_counts.find_by_guild(guild_id, window_months(since)[0])
    totals = window_totals(months, since, {
        metric_key("value.blocked_by_permission", key): "refused",
        **{metric_key(event, key): "delivered" for event in analytics.value_events()},
    })

    return health_line(
        spec,
        feature_problems(spec, views, guild, locale),
        totals.get("refused", 0),
        totals.get("delivered", 0),
        locale,
    )


async def send_feature_stats(
    interaction: discord.Interaction,
    command_key: str,
    lines: Sequence[str],
    views: Sequence[Any],
) -> None:
    """A feature's statistics, privately: its own lines, then its last days over `views`."""
    locale = parse_locale(interaction.locale)
    spec = setup_feature(command_key)
    if spec is None:
        raise KeyError(f"{command_key!r} is no command /setup offers")
    health = await feature_health(
        interaction.guild, str(interaction.guild_id), locale, spec, views
    )
    description = "\n\n".join(part for part in ("\n".join(lines), health) if part)
    embed = base_embed(
        command_text(spec, "command.stats.title", locale),
        description,
        footer=standard_footer(locale),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


def figure_lines(command_key: str, figures: Sequence[Figure], locale: str) -> List[str]:
    """The figures as a feature's statistics draw them, `emoji **label:** value` with
    the label from its `command.stats.figures`, or its `command.stats.empty` alone
    while there is none."""
    stats = i18n.block(f"{command_key}.command.stats", Stats)
    language = parse_locale(locale)
    if not figures:
        return [stats.empty.get(language)] if stats.empty else []
    lines = []
    for figure in figures:
        if figure.name not in stats.figures:
            raise CopyError(f"{command_key}.command.stats.figures writes no {figure.name}")
        label = stats.figures[figure.name].get(language)
        lines.append(f"{figure.emoji} **{label}:** {figure.value}")
    return lines

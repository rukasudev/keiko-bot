"""How each feature did in its last days: what Keiko is missing for it, the deliveries
it refused or made, and the statistics embed every manager panel opens."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Sequence

import discord

from app.components.embed import base_embed
from app.constants import Commands as commands_constants
from app.core import analytics
from app.core.analytics_reports import window_months, window_totals
from app.core.analytics_sink import metric_key
from app.data.analytics import counts
from app.core.cogs import feature_spec
from app.core.utils import fill, ml, parse_locale, stored_value


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


def feature_problems(spec: Dict[str, Any], views: Any, guild: Any, locale: str) -> List[str]:
    """What Keiko is missing for one feature, one line each."""
    base = "commands.commands.setup.permissions"
    me = guild.me
    problems: List[str] = []

    def name(permission: str) -> str:
        return ml(f"{base}.names.{permission}", locale)

    for permission in spec.get("server_permissions", ()):
        if not getattr(me.guild_permissions, permission, False):
            problems.append(
                fill(ml(f"{base}.server", locale), permission=name(permission))
            )

    channel_permissions = spec.get("channel_permissions", ())
    if channel_permissions:
        for channel_id in _ids_by_style(views, "channel"):
            channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
            if channel is None:
                problems.append(ml(f"{base}.channel-gone", locale))
                continue
            granted = channel.permissions_for(me)
            for permission in channel_permissions:
                if not getattr(granted, permission, False):
                    problems.append(
                        fill(
                            ml(f"{base}.channel", locale),
                            permission=name(permission),
                            channel=channel_id,
                        )
                    )

    if spec.get("assigns_roles"):
        for role_id in _ids_by_style(views, "role"):
            role = guild.get_role(int(role_id)) if role_id.isdigit() else None
            if role is not None and role.position >= me.top_role.position:
                problems.append(
                    fill(ml(f"{base}.role-above", locale), role=role_id)
                )

    return problems


def _one_or_many(count: int) -> str:
    return "one" if count == 1 else "many"


def health_line(
    spec: Dict[str, Any], problems: List[str], refused: int, delivered: int, locale: str
) -> str:
    """One feature's last days in a sentence: a problem first, then the deliveries it
    refused, then the ones it made, else a quiet line."""
    base = "commands.commands.commons.stats.recent"
    days = commands_constants.FEATURE_STATS_DAYS
    if problems:
        return problems[0]
    if refused:
        return fill(ml(f"{base}.refused-{_one_or_many(refused)}", locale), count=refused, days=days)
    if delivered:
        key = f"{base}.delivered-{_one_or_many(delivered)}.{spec['button_key']}"
        return fill(ml(key, locale), count=delivered, days=days)
    return fill(ml(f"{base}.quiet", locale), days=days)


async def feature_health(
    guild: Any, guild_id: str, user_id: str, locale: str, spec: Dict[str, Any]
) -> str:
    """How one feature did in its last days, in a sentence: the problem first."""
    from app.settings.features import saved_settings

    key = spec["command_key"]
    since = datetime.now(timezone.utc) - timedelta(days=commands_constants.FEATURE_STATS_DAYS)
    months, views = await asyncio.gather(
        asyncio.to_thread(counts.find_by_guild, guild_id, window_months(since)[0]),
        saved_settings(key, guild_id, user_id, locale, guild),
    )
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
    interaction: discord.Interaction, command_key: str, lines: Sequence[str]
) -> None:
    """A feature's statistics, privately: its own lines, then its last days."""
    locale = parse_locale(interaction.locale)
    spec = feature_spec(command_key)
    guild_id = str(interaction.guild_id)
    health = await feature_health(
        interaction.guild, guild_id, str(interaction.user.id), locale, spec
    )
    description = "\n\n".join(part for part in ("\n".join(lines), health) if part)
    embed = base_embed(
        ml(f"commands.commands.commons.stats.title.{spec['button_key']}", locale),
        description,
        footer=ml("commands.commands.commons.embed.footer", locale=locale),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

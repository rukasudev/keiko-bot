"""What the /setup card's buttons open: the server history and the permissions report."""
from typing import Any, Dict, List

import discord

from app.components.embed import base_embed
from app.constants import Commands as commands_constants
from app.data.cogs import find_cog_events_by_guild_id_async
from app.data.moderations import find_moderations_by_guild_async
from app.services.manager import parse_history_data
from app.services.trace import as_utc
from app.services.utils import ml, parse_locale


def _spec(command_key: str) -> Dict[str, Any]:
    return next(
        (f for f in commands_constants.SETUP_FEATURES if f["command_key"] == command_key),
        {},
    )


def feature_label(spec: Dict[str, Any], locale: str) -> str:
    """A feature's emoji and name, as the /setup card shows it."""
    name = ml(f"buttons.setup.{spec['button_key']}.label", locale)
    return f"{spec['emoji']} {name}"


async def guild_history(guild_id: str) -> List[Dict[str, Any]]:
    """Every feature's audit records for the server, newest first."""
    records: List[Dict[str, Any]] = []
    for spec in commands_constants.SETUP_FEATURES:
        records.extend(
            await find_cog_events_by_guild_id_async(guild_id, spec["command_key"])
        )
    return sorted(records, key=lambda record: as_utc(record["datetime"]), reverse=True)


def history_fields(
    records: List[Dict[str, Any]], interaction: discord.Interaction
) -> Dict[str, str]:
    """The records as history fields, every line led by the feature it happened in."""
    locale = parse_locale(interaction.locale)

    def label_for(cog_key: str) -> str:
        spec = _spec(cog_key)
        return f"**{feature_label(spec, locale)}**" if spec else f"`{cog_key}`"

    return parse_history_data(records, interaction, label_for=label_for)


async def send_history(interaction: discord.Interaction) -> None:
    """The server's recent changes across every feature, privately."""
    from app.views.records import RecordsBrowser

    locale = parse_locale(interaction.locale)
    records = await guild_history(str(interaction.guild.id))
    browser = RecordsBrowser(
        fetch=lambda _interaction, _user: records,
        to_fields=history_fields,
        title=ml("buttons.changes-history.label", locale),
        description=ml("commands.commands.setup.history.desc", locale),
        empty_description=ml("commands.commands.setup.history.empty", locale),
    )
    await browser.send(interaction)


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
                        found.extend(_ids(entry.get("_raw_value", entry.get("value"))))
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
                ml(f"{base}.server", locale).replace("{permission}", name(permission))
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
                        ml(f"{base}.channel", locale)
                        .replace("{permission}", name(permission))
                        .replace("{channel}", channel_id)
                    )

    if spec.get("assigns_roles"):
        for role_id in _ids_by_style(views, "role"):
            role = guild.get_role(int(role_id)) if role_id.isdigit() else None
            if role is not None and role.position >= me.top_role.position:
                problems.append(ml(f"{base}.role-above", locale).replace("{role}", role_id))

    return problems


async def permission_report(
    guild: Any, guild_id: str, user_id: str, locale: str
) -> discord.Embed:
    """Keiko's missing permissions for each configured feature, as an embed."""
    from app.forms.definitions.registry import registry
    from app.forms.engine.summary import responses
    from app.forms.features import feature_for
    from app.forms.features.protocol import OpenContext

    base = "commands.commands.setup.permissions"
    title = ml(f"{base}.title", locale)
    moderations = await find_moderations_by_guild_async(guild_id) or {}
    configured = [
        spec
        for spec in commands_constants.SETUP_FEATURES
        if moderations.get(spec["command_key"])
    ]
    if not configured:
        return base_embed(title, ml(f"{base}.nothing", locale))

    embed = base_embed(title, ml(f"{base}.desc", locale))
    for spec in configured:
        feature = feature_for(spec["command_key"])
        opened = await feature.open(OpenContext(guild_id, user_id, locale, guild, None))
        close = getattr(opened.pending_previews, "close", None)
        if close is not None:
            close()
        answers = feature.from_document(opened.document or {})
        views = responses(registry.get(spec["command_key"]).steps, answers, locale)
        problems = feature_problems(spec, views, guild, locale)
        embed.add_field(
            name=feature_label(spec, locale),
            value="\n".join(problems) or ml(f"{base}.ok", locale),
            inline=False,
        )
    return embed


async def send_permissions(interaction: discord.Interaction) -> None:
    """The permissions report for the server, privately."""
    locale = parse_locale(interaction.locale)
    embed = await permission_report(
        interaction.guild, str(interaction.guild.id), str(interaction.user.id), locale
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

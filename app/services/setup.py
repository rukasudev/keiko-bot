"""The /setup card: every feature Keiko offers, its status and what its buttons open."""
import asyncio
from typing import Any, Dict, List

import discord

from app.components.embed import base_embed, response_embed
from app.constants import Commands as commands_constants
from app.data.cogs import find_cog_events_by_guild_id_async
from app import logger
from app.components.buttons import GenericButton
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.data.cogs import find_cog_by_guild_id_async
from app.settings.discord import layout
from app.data.moderations import find_moderations_by_guild_async
from app.services import analytics
from app.services.manager import parse_history_data
from app.services.trace import as_utc
from app.services.utils import fill, is_guild_admin, ml, parse_locale, stored_value


def _spec(command_key: str) -> Dict[str, Any]:
    return next(
        (
            spec
            for spec in commands_constants.SETUP_FEATURES
            if spec["command_key"] == command_key
        ),
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


async def permission_report(
    guild: Any, guild_id: str, user_id: str, locale: str
) -> discord.Embed:
    """Keiko's missing permissions for each configured feature, as an embed."""
    from app.settings.features import saved_settings

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
        views = await saved_settings(
            spec["command_key"], guild_id, user_id, locale, guild
        )
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


def _translated_command(command_key: str, locale: str) -> str:
    info = commands_constants.FEATURE_COMMANDS[command_key]
    group = ml(f"commands.groups.{info['group']}", locale)
    subgroup = ml(f"commands.commands.{info['namespace']}.subgroup", locale)
    name = ml(f"commands.commands.{info['namespace']}.name", locale)
    return f"/{group} {subgroup} {name}"


async def _state(guild_id: str, command_key: str, moderations: dict) -> str:
    if not moderations.get(command_key, False):
        return "pending"
    cog_data = await find_cog_by_guild_id_async(guild_id, command_key)
    if cog_data and not cog_data.get(commands_constants.ENABLED_KEY, True):
        return "paused"
    return "configured"


def _row_text(feature: dict, state: str, locale: str) -> str:
    base = "commands.commands.setup.embed"
    button_key = feature["button_key"]
    name = ml(f"buttons.setup.{button_key}.label", locale)
    command = _translated_command(feature["command_key"], locale)
    details = [ml(f"{base}.features.{button_key}", locale), f"`{command}`"]
    if state == "paused":
        details.insert(0, ml(f"{base}.paused", locale))
    return f"{feature['emoji']} **{name}**\n-# " + " · ".join(details)


async def _open_commands(interaction: discord.Interaction) -> None:
    from app.services.help import send_help

    await send_help(interaction, "setup_dashboard", ephemeral=True)


async def _open_history(interaction: discord.Interaction) -> None:
    from app.services.setup import send_history

    await send_history(interaction)


async def _open_permissions(interaction: discord.Interaction) -> None:
    from app.services.setup import send_permissions

    await send_permissions(interaction)


def _card_buttons(locale: str) -> list:
    grey = discord.ButtonStyle.secondary
    return [
        GenericButton(ml("buttons.setup.commands.label", locale), _open_commands, grey, emoji="📚"),
        GenericButton(ml("buttons.history.label", locale), _open_history, grey, emoji="📜"),
        GenericButton(ml("buttons.setup.permissions.label", locale), _open_permissions, grey, emoji="🩺"),
        discord.ui.Button(
            label=ml("buttons.setup.support.label", locale),
            emoji="💬",
            style=discord.ButtonStyle.link,
            url=commands_constants.SUPPORT_SERVER_URL,
        ),
    ]


class SetupFeatureButton(discord.ui.Button):
    """Opens one feature from the /setup card."""

    def __init__(self, command_key: str, label: str, configured: bool):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary if configured else discord.ButtonStyle.success,
        )
        self.command_key = command_key

    async def callback(self, interaction: discord.Interaction):
        from app.components.buttons import run_feature_command

        await run_feature_command(interaction, self.command_key, "setup_dashboard")


class SetupView(discord.ui.LayoutView):
    """The /setup card as a view."""

    def __init__(self) -> None:
        super().__init__(timeout=view_constants.SHORT_TIMEOUT_SECONDS)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"SetupView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


async def open_setup_dashboard(interaction: discord.Interaction, source: str) -> None:
    """Send the /setup card privately to an admin, or say that it needs one."""
    locale = parse_locale(interaction.locale)
    if interaction.guild is None or not is_guild_admin(interaction.user):
        embed = response_embed("buttons.setup.admin-only", locale)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    analytics.emit(
        "dashboard.opened",
        guild_id=interaction.guild_id,
        user_id=interaction.user.id,
        source=source,
    )
    view = await setup_dashboard(str(interaction.guild.id), locale)
    await interaction.response.send_message(view=view, ephemeral=True)


def setup_dashboard_button(locale: str, source: str) -> discord.ui.Button:
    """A button that opens the /setup card from another screen."""

    async def open_dashboard(interaction: discord.Interaction) -> None:
        await open_setup_dashboard(interaction, source)

    return GenericButton(
        ml("buttons.setup.dashboard.label", locale),
        open_dashboard,
        discord.ButtonStyle.primary,
        emoji="🔧",
    )


async def setup_dashboard(guild_id: str, locale: str) -> discord.ui.LayoutView:
    """The /setup card for this guild, in the admin's language."""
    base = "commands.commands.setup.embed"
    moderations = await find_moderations_by_guild_async(guild_id) or {}
    features = commands_constants.SETUP_FEATURES
    read = [_state(guild_id, feature["command_key"], moderations) for feature in features]
    states = list(zip(features, await asyncio.gather(*read)))
    pending = [(feature, state) for feature, state in states if state == "pending"]
    configured = [(feature, state) for feature, state in states if state != "pending"]
    groups = (
        ("pending", pending, "buttons.setup.start.label"),
        ("configured", configured, "buttons.setup.manage.label"),
    )

    card = layout.container()
    intro = ml(f"{base}.desc" if pending else f"{base}.all-configured", locale)
    layout.header(
        card,
        ml(f"{base}.title", locale),
        intro,
        KeikoIcons.IMAGE_01,
        note=ml(f"{base}.note", locale),
    )
    for group_key, members, label_key in groups:
        if not members:
            continue
        title = fill(ml(f"{base}.{group_key}", locale), count=len(members))
        layout.row(card, f"### {title}")
        for feature, state in members:
            button = SetupFeatureButton(
                feature["command_key"], ml(label_key, locale), group_key == "configured"
            )
            layout.row(card, _row_text(feature, state, locale), button, separated=False)
    layout.footer(card, f"• {ml(f'{base}.footer', locale)}")

    view = SetupView()
    view.add_item(card)
    view.add_item(discord.ui.ActionRow(*_card_buttons(locale)))
    return view

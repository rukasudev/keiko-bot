"""The /setup card: its first page, a page per category, the server settings, and
what each of their buttons opens."""
import asyncio
from math import ceil
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import discord

from app import logger
from app.components.buttons import GenericButton
from app.components.embed import (
    base_embed,
    card_footer,
    card_frame,
    card_header,
    card_row,
    response_embed,
    response_error_embed,
)
from app.components.select import Select
from app.constants import Commands
from app.constants import KeikoIcons, supported_locales
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.data.guild.cogs import find_cog_by_guild_id_async
from app.exceptions import InvalidServerSetting
from app.integrations.google_translate import GoogleTranslate
from app.core import analytics
from app.core.cogs import feature_spec, find_changes, is_feature_on
from app.core.manager import parse_history_data
from app.core.feature_health import feature_problems
from app.core.servers import (
    interaction_language,
    save_server_setting,
    server_language,
    server_settings,
)
from app.core.utils import fill, is_guild_admin, ml

Row = Tuple[Dict[str, Any], str]
Opens = Callable[[discord.Interaction], Awaitable[None]]


def feature_label(spec: Dict[str, Any], locale: str) -> str:
    """A feature's emoji and name, as the /setup card shows it."""
    name = ml(f"buttons.setup.{spec['button_key']}.label", locale)
    return f"{spec['emoji']} {name}"


async def guild_history(guild_id: str) -> List[Dict[str, Any]]:
    """The server's changes, every feature's and its settings', newest first."""
    return await asyncio.to_thread(find_changes, guild_id)


def history_fields(
    records: List[Dict[str, Any]], interaction: discord.Interaction, locale: str
) -> Dict[str, str]:
    """The records as history fields, every line led by the feature it happened in."""

    def label_for(cog_key: str) -> str:
        if cog_key == Commands.SERVER_SETTINGS_KEY:
            return ml("commands.commands.setup.settings.row", locale)
        spec = feature_spec(cog_key)
        return f"**{feature_label(spec, locale)}**" if spec else f"`{cog_key}`"

    return parse_history_data(records, interaction, label_for=label_for)


async def send_history(interaction: discord.Interaction) -> None:
    """The server's recent changes across every feature, privately."""
    from app.views.records import RecordsBrowser

    locale = await interaction_language(interaction)
    records = await guild_history(str(interaction.guild.id))
    browser = RecordsBrowser(
        fetch=lambda _interaction, _user: records,
        to_fields=lambda shown, clicked: history_fields(shown, clicked, locale),
        title=ml("buttons.changes-history.label", locale),
        description=ml("commands.commands.setup.history.desc", locale),
        empty_description=ml("commands.commands.setup.history.empty", locale),
    )
    await browser.send(interaction)


async def permission_report(
    guild: Any, guild_id: str, user_id: str, locale: str
) -> discord.Embed:
    """Keiko's missing permissions for each configured feature, as an embed."""
    from app.settings.features import saved_settings

    base = "commands.commands.setup.permissions"
    title = ml(f"{base}.title", locale)
    configured = [spec for spec, state in await _states(guild_id) if state == "configured"]
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
    locale = await interaction_language(interaction)
    embed = await permission_report(
        interaction.guild, str(interaction.guild.id), str(interaction.user.id), locale
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


def _translated_command(command_key: str, locale: str) -> str:
    info = Commands.FEATURE_COMMANDS[command_key]
    group = ml(f"commands.groups.{info['group']}", locale)
    subgroup = ml(f"commands.commands.{info['namespace']}.subgroup", locale)
    name = ml(f"commands.commands.{info['namespace']}.name", locale)
    return f"/{group} {subgroup} {name}"


async def _state(guild_id: str, command_key: str) -> str:
    document = await find_cog_by_guild_id_async(guild_id, command_key)
    if not document:
        return "pending"
    return "configured" if is_feature_on(document) else "paused"


async def _states(guild_id: str) -> List[Row]:
    features = Commands.SETUP_FEATURES
    read = [_state(guild_id, feature["command_key"]) for feature in features]
    return list(zip(features, await asyncio.gather(*read)))


def _categories(states: List[Row]) -> List[Tuple[str, List[Row]]]:
    """The features of each category, in /setup order, empty categories left out."""
    grouped = []
    for category in Commands.SETUP_CATEGORIES:
        members = [(feature, state) for feature, state in states if feature["category"] == category]
        if members:
            grouped.append((category, members))
    return grouped


def _row_text(feature: dict, state: str, locale: str) -> str:
    base = "commands.commands.setup.embed"
    button_key = feature["button_key"]
    name = ml(f"buttons.setup.{button_key}.label", locale)
    command = _translated_command(feature["command_key"], locale)
    if state != "pending":
        name = f"{name} {ml(f'{base}.states.{state}', locale)}"
    details = [ml(f"{base}.features.{button_key}", locale), f"`{command}`"]
    return f"{feature['emoji']} **{name}**\n-# " + " · ".join(details)


def _category_text(category: str, members: List[Row], locale: str) -> str:
    base = "commands.commands.setup.embed"
    emoji = Commands.SETUP_CATEGORIES[category]
    name = ml(f"{base}.categories.{category}", locale)
    shown = view_constants.SETUP_CATEGORY_EXAMPLES
    examples = [
        f"- `{_translated_command(feature['command_key'], locale)}`"
        for feature, _state in members[:shown]
    ]
    hidden = len(members) - len(examples)
    if hidden:
        examples.append(f"- {fill(ml('commands.resume.more', locale), count=hidden)}")
    brief = ml(f"{base}.category-briefs.{category}", locale)
    heading = ml(f"{base}.top-commands", locale)
    return "\n".join([f"{emoji} **{name}**", brief, "", heading, *examples])


def _language_label(language: str) -> str:
    return GoogleTranslate.get_language_with_flag(language)


def _timezone_label(zone: str, locale: str) -> str:
    slug = Commands.SERVER_TIMEZONES.get(zone)
    if slug is None:
        return zone
    return ml(f"commands.commands.setup.settings.timezones.{slug}", locale)


def _settings_text(locale: str) -> str:
    base = "commands.commands.setup.settings"
    return f"{ml(f'{base}.row', locale)}\n{ml(f'{base}.brief', locale)}"


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
        GenericButton(ml("buttons.help.label", locale), _open_commands, grey, emoji="📚"),
        GenericButton(ml("buttons.history.label", locale), _open_history, grey, emoji="📜"),
        GenericButton(ml("buttons.setup.permissions.label", locale), _open_permissions, grey, emoji="🩺"),
        discord.ui.Button(
            label=ml("buttons.setup.support.label", locale),
            emoji="💬",
            style=discord.ButtonStyle.link,
            url=Commands.SUPPORT_SERVER_URL,
        ),
    ]


def _navigation(
    label_key: str,
    opens: Opens,
    locale: str,
    style: discord.ButtonStyle = discord.ButtonStyle.primary,
    **options: Any,
) -> GenericButton:
    return GenericButton(ml(label_key, locale), opens, style, **options)


def _back(locale: str) -> GenericButton:
    grey = discord.ButtonStyle.secondary
    return _navigation("buttons.back.label", show_first_page, locale, grey)


def _footer(card: discord.ui.Container, locale: str) -> None:
    card_footer(card, f"• {ml('commands.commands.setup.embed.footer', locale)}")


def _ending(interaction: discord.Interaction, command_key: str, category: str):
    guild_id = str(interaction.guild.id)

    async def ending() -> discord.ui.LayoutView:
        return await category_page(guild_id, category, activated=command_key)

    return ending


def _came_from(interaction: discord.Interaction, category: str, page: int):
    guild_id = str(interaction.guild.id)

    async def back() -> discord.ui.LayoutView:
        return await category_page(guild_id, category, page)

    return back


class SetupFeatureButton(discord.ui.Button):
    """Opens one feature from a category page: its setup's first screen goes back to
    that page, and a setup saved ends on the page that holds the feature."""

    def __init__(self, command_key: str, label: str, configured: bool, category: str, page: int):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary if configured else discord.ButtonStyle.primary,
        )
        self.command_key = command_key
        self.category = category
        self.page = page

    async def callback(self, interaction: discord.Interaction):
        from app.components.buttons import run_feature_command

        await run_feature_command(
            interaction,
            self.command_key,
            "setup_dashboard",
            ending=_ending(interaction, self.command_key, self.category),
            back=_came_from(interaction, self.category, self.page),
        )


def _feature_button(
    feature: Dict[str, Any], state: str, locale: str, category: str, page: int
) -> SetupFeatureButton:
    label_key = "buttons.setup.start.label" if state == "pending" else "buttons.setup.manage.label"
    configured = state != "pending"
    return SetupFeatureButton(feature["command_key"], ml(label_key, locale), configured, category, page)


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


def _view(card: discord.ui.Container, *buttons: discord.ui.Item) -> SetupView:
    view = SetupView()
    view.add_item(card)
    view.add_item(discord.ui.ActionRow(*buttons))
    return view


async def open_setup_dashboard(interaction: discord.Interaction, source: str) -> None:
    """Send the /setup card privately to an admin, or say that it needs one."""
    if interaction.guild is None or not is_guild_admin(interaction.user):
        embed = response_embed("buttons.setup.admin-only", await interaction_language(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    analytics.record(
        "dashboard.opened",
        guild_id=interaction.guild_id,
        actor=analytics.actor("user", interaction.user.id),
        props={"source": source},
    )
    view = await setup_dashboard(str(interaction.guild.id))
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


async def setup_dashboard(guild_id: str) -> discord.ui.LayoutView:
    """The /setup first page for this guild in its language: its server settings and
    its categories."""
    base = "commands.commands.setup.embed"
    states, locale = await asyncio.gather(_states(guild_id), server_language(guild_id))
    pending = any(state == "pending" for _feature, state in states)
    intro = ml(f"{base}.desc" if pending else f"{base}.all-configured", locale)

    card = card_frame()
    card_header(card, ml(f"{base}.title", locale), intro, KeikoIcons.IMAGE_01)
    opens = _navigation("buttons.setup.start.label", open_settings, locale)
    card_row(card, _settings_text(locale), opens)
    for category, members in _categories(states):
        opens = _category_opener(category, 0)
        card_row(
            card,
            _category_text(category, members, locale),
            _navigation("buttons.setup.start.label", opens, locale),
        )
    _footer(card, locale)
    return _view(card, *_card_buttons(locale))


async def show_first_page(interaction: discord.Interaction) -> None:
    """Turn the message back into the /setup first page."""
    view = await setup_dashboard(str(interaction.guild.id))
    await interaction.response.edit_message(view=view)


def _category_opener(category: str, page: int) -> Opens:
    async def opens(interaction: discord.Interaction) -> None:
        await show_category(interaction, category, page)

    return opens


async def show_category(interaction: discord.Interaction, category: str, page: int = 0) -> None:
    """Turn the message into one page of a category, read once it was answered."""
    if not interaction.response.is_done():
        await interaction.response.defer()
    view = await category_page(str(interaction.guild.id), category, page)
    await interaction.edit_original_response(view=view)


async def category_page(
    guild_id: str, category: str, page: int = 0, activated: Optional[str] = None
) -> discord.ui.LayoutView:
    """One page of a category's features, in the guild's language; after `activated`
    was set up, the page that holds it, its title saying it was just turned on."""
    states, locale = await asyncio.gather(_states(guild_id), server_language(guild_id))
    members = [
        (feature, "activated" if feature["command_key"] == activated else state)
        for feature, state in dict(_categories(states)).get(category, [])
    ]
    size = view_constants.SETUP_PAGE_SIZE
    pages = max(1, ceil(len(members) / size))
    keys = [feature["command_key"] for feature, _state in members]
    if activated in keys:
        page = keys.index(activated) // size
    page = min(max(page, 0), pages - 1)
    shown = members[page * size:(page + 1) * size]
    return category_view(category, shown, page, pages, locale)


def category_view(
    category: str,
    rows: List[Row],
    page: int,
    pages: int,
    locale: str,
) -> discord.ui.LayoutView:
    """The page drawn: the category's header, its features, the page turns and Voltar."""
    base = "commands.commands.setup"
    note = ""
    if pages > 1:
        note = fill(ml(f"{base}.category.page", locale), page=page + 1, pages=pages)
    emoji = Commands.SETUP_CATEGORIES[category]
    title = f"{emoji} {ml(f'{base}.embed.categories.{category}', locale)}"

    card = card_frame()
    intro = ml(f"{base}.category.intro", locale)
    card_header(card, title, intro, KeikoIcons.IMAGE_01, note)
    card.add_item(discord.ui.Separator())
    for feature, state in rows:
        button = _feature_button(feature, state, locale, category, page)
        card_row(card, _row_text(feature, state, locale), button, separated=False)
    _footer(card, locale)

    buttons = []
    if pages > 1:
        buttons += [
            _navigation(
                "buttons.previous.label",
                _category_opener(category, page - 1),
                locale,
                emoji="◀️",
                disabled=page == 0,
            ),
            _navigation(
                "buttons.next.label",
                _category_opener(category, page + 1),
                locale,
                emoji="▶️",
                disabled=page == pages - 1,
            ),
        ]
    buttons.append(_back(locale))
    return _view(card, *buttons)


async def open_settings(interaction: discord.Interaction) -> None:
    """Turn the message into the server settings screen."""
    view = await settings_screen(str(interaction.guild.id))
    await interaction.response.edit_message(view=view)


def _setting_select(key: str, placeholder: str, options: Dict[str, str]) -> Select:
    select: Select

    async def chosen(interaction: discord.Interaction) -> None:
        await change_setting(interaction, key, select.values[0])

    select = Select(placeholder, options, custom_callback=chosen, unique=1)
    return select


async def settings_screen(guild_id: str) -> discord.ui.LayoutView:
    """The server settings in the server's language, one row per value with what
    changes it, then Voltar."""
    base = "commands.commands.setup.settings"
    settings = await asyncio.to_thread(server_settings, guild_id)
    locale = settings[Commands.SERVER_LANGUAGE_KEY]
    languages = {value.lower(): _language_label(value) for value in supported_locales}
    zones = {value: _timezone_label(value, locale) for value in Commands.SERVER_TIMEZONES}
    prefixes = {value: value for value in Commands.SERVER_PREFIXES}

    card = card_frame()
    card_header(card, ml(f"{base}.title", locale), ml(f"{base}.desc", locale), KeikoIcons.IMAGE_01)
    language, zone, prefix = (settings[key] for key in Commands.SERVER_SETTING_KEYS)
    rows = (
        (Commands.SERVER_LANGUAGE_KEY, "language", _language_label(language), languages),
        (Commands.SERVER_TIMEZONE_KEY, "timezone", _timezone_label(zone, locale), zones),
        (Commands.SERVER_PREFIX_KEY, "prefix", f"`{prefix}`", prefixes),
    )
    for key, name, shown, options in rows:
        card_row(card, fill(ml(f"{base}.{name}", locale), value=shown, prefix=prefix))
        placeholder = ml(f"{base}.{name}-placeholder", locale)
        card.add_item(discord.ui.ActionRow(_setting_select(key, placeholder, options)))
    _footer(card, locale)
    return _view(card, _back(locale))


async def change_setting(interaction: discord.Interaction, key: str, value: str) -> None:
    """Save one server setting an admin picked, then draw the settings screen again."""
    locale = await interaction_language(interaction)
    if interaction.guild is None or not is_guild_admin(interaction.user):
        embed = response_embed("buttons.setup.admin-only", locale)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    await interaction.response.defer()
    try:
        await asyncio.to_thread(save_server_setting, guild_id, key, value)
    except InvalidServerSetting as refused:
        embed = response_error_embed(f"server-setting-refused.{refused.key}", locale)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    analytics.record(
        "config.changed",
        guild_id=guild_id,
        cog=Commands.SERVER_SETTINGS_KEY,
        actor=analytics.actor("user", interaction.user.id),
        props={"source": "setup_dashboard", "changed_keys": [key], "changed_count": 1},
    )
    await analytics.written()
    await interaction.edit_original_response(view=await settings_screen(guild_id))

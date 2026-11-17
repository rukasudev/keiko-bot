"""The /setup card: its first page, a page per category, the server settings, what
each of their buttons opens, and the greeting that leads a new server to it."""

import asyncio
from math import ceil
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import discord

from app import logger
from app.constants import Commands, KeikoIcons, supported_locales
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.core import analytics
from app.core.cogs import find_changes, is_feature_on
from app.core.commands import (
    PARENT_GROUPS,
    Command,
    GuildJoined,
    ParentGroup,
    command_for,
    opener,
    setup_features,
)
from app.core.facts import Facts, facts
from app.core.feature_health import feature_problems
from app.core.manager import change_label, parse_history_data
from app.core.servers import (
    interaction_language,
    save_server_setting,
    server_language,
    server_settings,
)
from app.core.utils import (
    command_text,
    fill,
    is_guild_admin,
    join_names,
    ml,
    parse_locale,
)
from app.data import data
from app.exceptions import InvalidServerSetting
from app.integrations.google_translate import GoogleTranslate
from app.ui.buttons import GenericButton
from app.ui.embed import (
    card_copy,
    card_footer,
    card_frame,
    card_header,
    card_row,
    response_embed,
    response_error_embed,
)
from app.ui.select import Select

Row = Tuple[Command, str]
Opens = Callable[[discord.Interaction], Awaitable[None]]
Page = Callable[[], Awaitable[discord.ui.LayoutView]]


def command_facts(**values: Any) -> Facts:
    """The facts this command's code passes its copy, each under its address."""
    return facts("setup", **values)


async def guild_history(guild_id: str) -> List[Dict[str, Any]]:
    """The server's changes, every feature's and its settings', newest first."""
    return await find_changes(guild_id)


def history_fields(
    records: List[Dict[str, Any]], interaction: discord.Interaction, locale: str
) -> Dict[str, str]:
    """The records as history fields, each named by the feature it happened in."""

    def label_for(cog_key: str) -> str:
        if cog_key == Commands.SERVER_SETTINGS_KEY:
            settings: str = ml("setup.ui.cards.settings.title", locale)
            return settings
        return change_label(cog_key, locale)

    return parse_history_data(records, interaction, label_for=label_for)


async def send_history(interaction: discord.Interaction) -> None:
    """The server's recent changes across every feature, privately."""
    from app.ui.records import RecordsBrowser

    locale = await interaction_language(interaction)
    browser = RecordsBrowser(
        fetch=lambda _interaction, _user: guild_history(_guild_id(interaction)),
        to_fields=lambda shown, clicked: history_fields(shown, clicked, locale),
        copy="setup.ui.records.history",
    )
    await browser.send(interaction)


async def permission_report(
    guild: Any, guild_id: str, user_id: str, locale: str
) -> discord.Embed:
    """Keiko's missing permissions for each configured feature, as an embed."""
    from app.forms.features import saved_settings

    configured = [
        spec for spec, state in await _states(guild_id) if state == "configured"
    ]
    embed = response_embed(
        "setup.ui.responses.permissions",
        locale,
        empty=not configured,
        thumbnail=KeikoIcons.IMAGE_02,
    )
    for spec in configured:
        views = await saved_settings(spec.key, guild_id, user_id, locale, guild)
        problems = feature_problems(spec, views, guild, locale)
        embed.add_field(
            name=change_label(spec.key, locale),
            value="\n".join(problems)
            or ml("setup.ui.responses.permissions.lines.ok", locale),
            inline=False,
        )
    return embed


async def send_permissions(interaction: discord.Interaction) -> None:
    """The permissions report for the server, privately."""
    locale = await interaction_language(interaction)
    embed = await permission_report(
        interaction.guild, _guild_id(interaction), str(interaction.user.id), locale
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


def _translated_command(command_key: str, locale: str) -> str:
    command = command_for(command_key)
    group = ml(f"texts.groups.{command.group}", locale)
    subgroup = command_text(command, "command.slash.subgroup", locale)
    name = command_text(command, "command.slash.name", locale)
    return f"/{group} {subgroup} {name}"


async def _state(guild_id: str, command_key: str) -> str:
    document = await data().guild_cogs.find_cog_by_guild_id(guild_id, command_key)
    if not document:
        return "pending"
    return "configured" if is_feature_on(document) else "paused"


async def _states(guild_id: str) -> List[Row]:
    features = setup_features()
    read = [_state(guild_id, feature.key) for feature in features]
    return list(zip(features, await asyncio.gather(*read)))


def _categories(states: List[Row]) -> List[Tuple[ParentGroup, List[Row]]]:
    grouped = []
    for category in PARENT_GROUPS:
        members = [
            (feature, state)
            for feature, state in states
            if feature.category == category
        ]
        if members:
            grouped.append((category, members))
    return grouped


def _row_text(feature: Command, state: str, locale: str) -> str:
    name = command_text(feature, "command.setup.label", locale)
    command = _translated_command(feature.key, locale)
    if state != "pending":
        name = f"{name} {ml(f'setup.ui.texts.states.{state}', locale)}"
    details = [
        command_text(feature, "command.setup.description", locale),
        f"`{command}`",
    ]
    return f"{feature.emoji} **{name}**\n-# " + " · ".join(details)


def _category_text(category: ParentGroup, members: List[Row], locale: str) -> str:
    base = "setup.ui.texts"
    emoji = PARENT_GROUPS[category]
    name = ml(f"{base}.categories.{category}", locale)
    shown = view_constants.SETUP_CATEGORY_EXAMPLES
    examples = [
        f"- `{_translated_command(feature.key, locale)}`"
        for feature, _state in members[:shown]
    ]
    hidden = len(members) - len(examples)
    if hidden:
        examples.append(f"- {fill(ml('texts.and_more', locale), count=hidden)}")
    brief = ml(f"{base}.briefs.{category}", locale)
    heading = ml(f"{base}.top_commands", locale)
    return "\n".join([f"{emoji} **{name}**", brief, "", heading, *examples])


def _language_label(language: str) -> str:
    return GoogleTranslate.get_language_with_flag(language)


def _timezone_label(zone: str, locale: str) -> str:
    slug = Commands.SERVER_TIMEZONES.get(zone)
    if slug is None:
        return zone

    label: str = ml(f"setup.ui.texts.timezones.{slug}", locale)
    return label


def _settings_text(locale: str) -> str:
    base = "setup.ui.cards.dashboard.rows.settings"
    return f"{ml(f'{base}.title', locale)}\n{ml(f'{base}.description', locale)}"


async def _open_commands(interaction: discord.Interaction) -> None:
    await opener("help")(interaction, "setup_dashboard")


def _guild_id(interaction: discord.Interaction) -> str:
    if interaction.guild is None:
        raise LookupError("the /setup card answers only inside a server")
    return str(interaction.guild.id)


def _card_buttons(locale: str) -> List[discord.ui.Item[Any]]:
    grey = discord.ButtonStyle.secondary
    return [
        GenericButton(
            ml("buttons.help.label", locale), _open_commands, grey, emoji="📚"
        ),
        GenericButton(
            ml("buttons.history.label", locale), send_history, grey, emoji="📜"
        ),
        GenericButton(
            ml("setup.ui.buttons.permissions.label", locale),
            send_permissions,
            grey,
            emoji="🩺",
        ),
        discord.ui.Button(
            label=ml("setup.ui.buttons.support.label", locale),
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


def _footer(card: discord.ui.Container[Any], line: str) -> None:
    if line:
        card_footer(card, line)


def _ending(
    interaction: discord.Interaction, command_key: str, category: ParentGroup
) -> Page:
    guild_id = _guild_id(interaction)

    async def ending() -> discord.ui.LayoutView:
        return await category_page(guild_id, category, activated=command_key)

    return ending


def _came_from(
    interaction: discord.Interaction, category: ParentGroup, page: int
) -> Page:
    guild_id = _guild_id(interaction)

    async def back() -> discord.ui.LayoutView:
        return await category_page(guild_id, category, page)

    return back


class SetupFeatureButton(discord.ui.Button[Any]):
    """Opens one feature from a category page: its setup's first screen goes back to
    that page, and a setup saved ends on the page that holds the feature."""

    def __init__(
        self,
        command_key: str,
        label: str,
        configured: bool,
        category: ParentGroup,
        page: int,
    ) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary
            if configured
            else discord.ButtonStyle.primary,
        )
        self.command_key = command_key
        self.category = category
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the feature, ending on its category page and going back to this one."""
        from app.forms import run_feature_command

        await run_feature_command(
            interaction,
            self.command_key,
            "setup_dashboard",
            ending=_ending(interaction, self.command_key, self.category),
            back=_came_from(interaction, self.category, self.page),
        )


def _feature_button(
    feature: Command, state: str, locale: str, category: ParentGroup, page: int
) -> SetupFeatureButton:
    label_key = (
        "setup.ui.buttons.start.label"
        if state == "pending"
        else "setup.ui.buttons.manage.label"
    )
    configured = state != "pending"
    return SetupFeatureButton(
        feature.key, ml(label_key, locale), configured, category, page
    )


class SetupView(discord.ui.LayoutView):
    """The /setup card as a view."""

    def __init__(self) -> None:
        super().__init__(timeout=view_constants.SHORT_TIMEOUT_SECONDS)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item[Any],
    ) -> None:
        """Log what failed on the card, with the interaction that reached it."""
        logger.error(
            f"SetupView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


def _view(card: discord.ui.Container[Any], *buttons: discord.ui.Item[Any]) -> SetupView:
    view = SetupView()
    view.add_item(card)
    view.add_item(discord.ui.ActionRow(*buttons))
    return view


async def open_setup_dashboard(interaction: discord.Interaction, source: str) -> None:
    """Send the /setup card privately to an admin, or say that it needs one."""
    if interaction.guild is None or not is_guild_admin(interaction.user):
        embed = response_embed(
            "commands.responses.admin_only", await interaction_language(interaction)
        )
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


async def setup_dashboard(guild_id: str) -> discord.ui.LayoutView:
    """The /setup first page for this guild in its language: its server settings and
    its categories."""
    states, locale = await asyncio.gather(_states(guild_id), server_language(guild_id))
    pending = any(state == "pending" for _feature, state in states)
    copy = card_copy(
        "setup.ui.cards.dashboard", locale, command_facts(all_configured=not pending)
    )

    card = card_frame()
    card_header(card, copy.title, copy.description, KeikoIcons.IMAGE_01)
    settings = _navigation("setup.ui.buttons.start.label", open_settings, locale)
    card_row(card, _settings_text(locale), settings)
    for category, members in _categories(states):
        opens = _category_opener(category, 0)
        card_row(
            card,
            _category_text(category, members, locale),
            _navigation("setup.ui.buttons.start.label", opens, locale),
        )
    _footer(card, copy.footer)
    return _view(card, *_card_buttons(locale))


async def show_first_page(interaction: discord.Interaction) -> None:
    """Turn the message back into the /setup first page."""
    view = await setup_dashboard(_guild_id(interaction))
    await interaction.response.edit_message(view=view)


def _category_opener(category: ParentGroup, page: int) -> Opens:
    async def opens(interaction: discord.Interaction) -> None:
        await show_category(interaction, category, page)

    return opens


async def show_category(
    interaction: discord.Interaction, category: ParentGroup, page: int = 0
) -> None:
    """Turn the message into one page of a category, read once it was answered."""
    if not interaction.response.is_done():
        await interaction.response.defer()
    view = await category_page(_guild_id(interaction), category, page)
    await interaction.edit_original_response(view=view)


async def category_page(
    guild_id: str, category: ParentGroup, page: int = 0, activated: Optional[str] = None
) -> discord.ui.LayoutView:
    """One page of a category's features, in the guild's language; after `activated`
    was set up, the page that holds it, its title saying it was just turned on."""
    states, locale = await asyncio.gather(_states(guild_id), server_language(guild_id))
    members = [
        (feature, "activated" if feature.key == activated else state)
        for feature, state in dict(_categories(states)).get(category, [])
    ]
    size = view_constants.SETUP_PAGE_SIZE
    pages = max(1, ceil(len(members) / size))
    keys = [feature.key for feature, _state in members]
    if activated in keys:
        page = keys.index(activated) // size
    page = min(max(page, 0), pages - 1)
    shown = members[page * size : (page + 1) * size]
    return category_view(category, shown, page, pages, locale)


def category_view(
    category: ParentGroup,
    rows: List[Row],
    page: int,
    pages: int,
    locale: str,
) -> discord.ui.LayoutView:
    """The page drawn: the category header, its features, the page turns and Voltar."""
    name = ml(f"setup.ui.texts.categories.{category}", locale)
    copy = card_copy(
        "setup.ui.cards.category",
        locale,
        command_facts(
            emoji=PARENT_GROUPS[category], name=name, page=page + 1, pages=pages
        ),
    )

    card = card_frame()
    note = copy.note if pages > 1 else ""
    card_header(card, copy.title, copy.description, KeikoIcons.IMAGE_01, note)
    card.add_item(discord.ui.Separator())
    for feature, state in rows:
        button = _feature_button(feature, state, locale, category, page)
        card_row(card, _row_text(feature, state, locale), button, separated=False)
    _footer(card, copy.footer)

    buttons = []
    if pages > 1:
        buttons += [
            _navigation(
                "setup.ui.buttons.previous.label",
                _category_opener(category, page - 1),
                locale,
                emoji="◀️",
                disabled=page == 0,
            ),
            _navigation(
                "setup.ui.buttons.next.label",
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
    view = await settings_screen(_guild_id(interaction))
    await interaction.response.edit_message(view=view)


def _setting_select(
    key: str, placeholder: str, options: Dict[str, str]
) -> Select[discord.ui.View]:
    select: Select[discord.ui.View]

    async def chosen(interaction: discord.Interaction) -> None:
        await change_setting(interaction, key, select.values[0])

    select = Select(placeholder, options, custom_callback=chosen, unique=1)
    return select


async def settings_screen(guild_id: str) -> discord.ui.LayoutView:
    """The server settings in the server's language, one row per value with what
    changes it, then Voltar."""
    base = "setup.ui.cards.settings"
    settings = await server_settings(guild_id)
    locale = settings[Commands.SERVER_LANGUAGE_KEY]
    languages = {value.lower(): _language_label(value) for value in supported_locales}
    zones = {
        value: _timezone_label(value, locale) for value in Commands.SERVER_TIMEZONES
    }
    prefixes = {value: value for value in Commands.SERVER_PREFIXES}

    copy = card_copy("setup.ui.cards.settings", locale)
    card = card_frame()
    card_header(card, copy.title, copy.description, KeikoIcons.IMAGE_01)
    language, zone, prefix = (settings[key] for key in Commands.SERVER_SETTING_KEYS)
    rows = (
        (
            Commands.SERVER_LANGUAGE_KEY,
            "language",
            _language_label(language),
            languages,
        ),
        (
            Commands.SERVER_TIMEZONE_KEY,
            "timezone",
            _timezone_label(zone, locale),
            zones,
        ),
        (Commands.SERVER_PREFIX_KEY, "prefix", f"`{prefix}`", prefixes),
    )
    for key, name, shown, options in rows:
        row = f"{base}.rows.{name}"
        body = fill(ml(f"{row}.description", locale), value=shown, prefix=prefix)
        card_row(card, body)
        placeholder = ml(f"{row}.placeholder", locale)
        card.add_item(discord.ui.ActionRow(_setting_select(key, placeholder, options)))
    _footer(card, copy.footer)
    return _view(card, _back(locale))


async def change_setting(
    interaction: discord.Interaction, key: str, value: str
) -> None:
    """Save one server setting an admin picked, then draw the settings screen again."""
    locale = await interaction_language(interaction)
    if interaction.guild is None or not is_guild_admin(interaction.user):
        embed = response_embed("commands.responses.admin_only", locale)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    await interaction.response.defer()
    try:
        await save_server_setting(guild_id, key, value)
    except InvalidServerSetting as refused:
        embed = response_error_embed(f"setup.ui.errors.refused_{refused.key}", locale)
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


class SetupButton(discord.ui.Button[Any]):
    """One feature's button of the greetings sent before beta.2, still answered."""

    def __init__(
        self, command_key: str, label: str, emoji: str, custom_id: str
    ) -> None:
        self.command_key = command_key
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.grey,
            custom_id=custom_id,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the feature for an admin, or say that it needs one."""
        locale = await interaction_language(interaction)
        if not is_guild_admin(interaction.user):
            embed = response_embed("commands.responses.admin_only", locale)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        from app.forms import run_feature_command

        await run_feature_command(interaction, self.command_key, "greeting_button")


class DashboardButton(discord.ui.Button[Any]):
    """The greeting's button that opens the /setup card."""

    def __init__(self, label: str, locale: str) -> None:
        self.locale = locale
        super().__init__(
            label=label,
            emoji="🔧",
            style=discord.ButtonStyle.primary,
            custom_id="greetings:setup:dashboard",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the /setup card from the greeting."""
        await interaction_language(interaction)
        await open_setup_dashboard(interaction, "greeting_button")


class CommandsButton(discord.ui.Button[Any]):
    """Opens the command list, like the Help button of /setup."""

    def __init__(self, label: str) -> None:
        super().__init__(
            label=label,
            emoji="📚",
            style=discord.ButtonStyle.secondary,
            custom_id="greetings:commands",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the command list from the greeting."""
        await interaction_language(interaction)
        await opener("help")(interaction, "greeting_button")


def _build_setup_buttons(locale: str) -> List[SetupButton]:
    buttons = []
    for feature in setup_features():
        label = command_text(feature, "command.setup.label", locale)
        custom_id = f"greetings:setup:{feature.key}"
        buttons.append(
            SetupButton(
                command_key=feature.key,
                label=label,
                emoji=feature.emoji,
                custom_id=custom_id,
            )
        )
    return buttons


def _build_language_select(locale: str, callback: Opens) -> Select[discord.ui.View]:
    options = {}
    for language in supported_locales:
        options[language] = GoogleTranslate().get_language_with_flag(language)

    placeholder = GoogleTranslate().get_language_with_flag(locale)
    return Select(
        placeholder,
        options,
        custom_callback=callback,
        custom_id="greetings:language_select",
        row=0,
    )


def _category_lines(locale: str) -> str:
    line = ml("setup.ui.responses.greeting.lines.category", locale)
    lines = []
    for category, emoji in PARENT_GROUPS.items():
        names = [
            command_text(feature, "command.setup.label", locale)
            for feature in setup_features()
            if feature.category == category
        ]
        name = ml(f"setup.ui.texts.categories.{category}", locale)
        lines.append(
            fill(line, emoji=emoji, name=name, features=join_names(names, locale))
        )
    return "\n".join(lines)


def greeting_embed(locale: str) -> discord.Embed:
    """The greeting a server gets when Keiko joins, in `locale`, each category
    naming the features /setup places in it."""
    return response_embed(
        "setup.ui.responses.greeting",
        locale,
        image=True,
        facts=command_facts(categories=_category_lines(locale)),
    )


class _PersistentGreeting(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item[Any],
    ) -> None:
        logger.error(
            f"GreetingsView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


class GreetingsView(_PersistentGreeting):
    """The greeting a new server gets: its language, the /setup card, the commands."""

    def __init__(self, locale: str = "en-us") -> None:
        super().__init__(timeout=None)
        self.locale = locale
        self.selected_options: List[str] = []
        self._build(locale)

    def _build(self, locale: str) -> None:
        self.add_item(_build_language_select(locale, self.language_callback))

        dashboard_label = ml("setup.ui.buttons.dashboard.label", locale)
        self.add_item(DashboardButton(label=dashboard_label, locale=locale))
        self.add_item(CommandsButton(ml("buttons.help.label", locale)))

    async def send(self, guild: discord.Guild) -> Optional[discord.Message]:
        """Greet `guild` on the first channel Keiko may write in, in its language."""
        locale = await server_language(str(guild.id))
        self.locale = locale

        self.clear_items()
        self._build(locale)

        embed = greeting_embed(locale)

        for channel in guild.text_channels:
            if not channel.permissions_for(guild.me).send_messages:
                continue
            sent = await channel.send(embed=embed, view=self)
            self._emit_greeting_sent(guild, channel_found=True)
            return sent

        self._emit_greeting_sent(guild, channel_found=False)
        return None

    def _emit_greeting_sent(self, guild: discord.Guild, channel_found: bool) -> None:
        analytics.record(
            "guild.greeting_sent",
            guild_id=guild.id,
            props={
                "features_offered": len(setup_features()),
                "channel_found": channel_found,
            },
        )

    async def language_callback(self, interaction: discord.Interaction) -> None:
        """Draw the greeting again in the language picked."""
        selected = parse_locale(self.selected_options[0])
        self.locale = selected

        new_embed = greeting_embed(selected)

        self.clear_items()
        self._build(selected)

        await interaction.response.edit_message(embed=new_embed, view=self)


class LegacyGreetingButtons(_PersistentGreeting):
    """The feature buttons of greetings sent before beta.2: registered, never sent."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        for button in _build_setup_buttons("en-us"):
            self.add_item(button)


async def greet_server(joined: GuildJoined) -> None:
    """Greet the server that added Keiko, leading it to /setup."""
    await GreetingsView().send(joined.guild)


def greeting_views() -> List[discord.ui.View]:
    """Every persistent view a greeting message may still carry a button of."""
    return [GreetingsView(), LegacyGreetingButtons()]

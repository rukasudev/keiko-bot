import discord

from app import logger
from app.components.embed import response_embed
from app.components.select import Select
from app.constants import Commands as commands_constants
from app.constants import LogTypes as logconstants
from app.constants import supported_locales
from app.integrations.google_translate import GoogleTranslate
from app.services import analytics
from app.services.moderations import interaction_language, server_language
from app.services.utils import fill, join_names, ml, parse_locale

class SetupButton(discord.ui.Button):
    """One feature's button of the greetings sent before beta.2, still answered."""

    def __init__(self, command_key: str, label: str, emoji: str, custom_id: str):
        self.command_key = command_key
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.grey,
            custom_id=custom_id,
        )

    async def callback(self, interaction: discord.Interaction):
        locale = await interaction_language(interaction)
        if not interaction.user.guild_permissions.administrator:
            embed = response_embed("buttons.setup.admin-only", locale)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        from app.components.buttons import run_feature_command

        await run_feature_command(interaction, self.command_key, "greeting_button")


class DashboardButton(discord.ui.Button):
    def __init__(self, label: str, locale: str):
        self.locale = locale
        super().__init__(
            label=label,
            emoji="🔧",
            style=discord.ButtonStyle.primary,
            custom_id="greetings:setup:dashboard",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        from app.services.setup import open_setup_dashboard

        await interaction_language(interaction)
        await open_setup_dashboard(interaction, "greeting_button")


class CommandsButton(discord.ui.Button):
    """Opens the command list, like the Help button of /setup."""

    def __init__(self, label: str):
        super().__init__(
            label=label,
            emoji="📚",
            style=discord.ButtonStyle.secondary,
            custom_id="greetings:commands",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        from app.services.help import send_help

        await interaction_language(interaction)
        await send_help(interaction, "greeting_button", ephemeral=True)


def _build_setup_buttons(locale: str):
    buttons = []
    for feature in commands_constants.SETUP_FEATURES:
        label = ml(f"buttons.setup.{feature['button_key']}.label", locale)
        custom_id = f"greetings:setup:{feature['command_key']}"
        buttons.append(
            SetupButton(
                command_key=feature["command_key"],
                label=label,
                emoji=feature["emoji"],
                custom_id=custom_id,
            )
        )
    return buttons


def _build_language_select(locale: str, callback):
    options = {}
    for loc in supported_locales:
        options[loc] = GoogleTranslate().get_language_with_flag(loc)

    placeholder = GoogleTranslate().get_language_with_flag(locale)
    return Select(
        placeholder,
        options,
        custom_callback=callback,
        custom_id="greetings:language_select",
        row=0,
    )


def _category_lines(locale: str) -> str:
    line = ml("messages.greetings-message.category", locale)
    lines = []
    for category, emoji in commands_constants.SETUP_CATEGORIES.items():
        names = [
            ml(f"buttons.setup.{feature['button_key']}.label", locale)
            for feature in commands_constants.SETUP_FEATURES
            if feature["category"] == category
        ]
        name = ml(f"commands.commands.setup.embed.categories.{category}", locale)
        lines.append(fill(line, emoji=emoji, name=name, features=join_names(names, locale)))
    return "\n".join(lines)


def greeting_embed(locale: str) -> discord.Embed:
    """The greeting a server gets when Keiko joins, in `locale`, each category
    naming the features /setup places in it."""
    embed = response_embed("messages.greetings-message", locale, image=True)
    embed.description = fill(embed.description, categories=_category_lines(locale))
    return embed


class _PersistentGreeting(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"GreetingsView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


class GreetingsView(_PersistentGreeting):
    def __init__(self, locale: str = "en-us"):
        super().__init__(timeout=None)
        self.locale = locale
        self._build(locale)

    def _build(self, locale: str):
        self.add_item(_build_language_select(locale, self.language_callback))

        dashboard_label = ml("buttons.setup.dashboard.label", locale)
        self.add_item(DashboardButton(label=dashboard_label, locale=locale))
        self.add_item(CommandsButton(ml("buttons.help.label", locale)))

    async def send(self, guild: discord.Guild):
        locale = await server_language(str(guild.id))
        self.locale = locale

        self.clear_items()
        self._build(locale)

        embed = greeting_embed(locale)

        for channel in guild.text_channels:
            if not channel.permissions_for(guild.me).send_messages:
                continue
            self._emit_greeting_sent(guild, channel_found=True)
            return await channel.send(embed=embed, view=self)

        self._emit_greeting_sent(guild, channel_found=False)

    def _emit_greeting_sent(self, guild: discord.Guild, channel_found: bool) -> None:
        """A greeting nobody could receive is the quietest onboarding failure."""
        analytics.emit(
            "guild.greeting_sent",
            guild_id=guild.id,
            features_offered=len(commands_constants.SETUP_FEATURES),
            channel_found=channel_found,
        )

    async def language_callback(self, interaction: discord.Interaction):
        selected = parse_locale(self.selected_options[0])
        self.locale = selected

        new_embed = greeting_embed(selected)

        self.clear_items()
        self._build(selected)

        await interaction.response.edit_message(embed=new_embed, view=self)


class LegacyGreetingButtons(_PersistentGreeting):
    """The feature buttons of the greetings sent before beta.2: registered, never sent."""

    def __init__(self):
        super().__init__(timeout=None)
        for button in _build_setup_buttons("en-us"):
            self.add_item(button)


def greeting_views() -> list:
    """Every persistent view a greeting message may still carry a button of."""
    return [GreetingsView(), LegacyGreetingButtons()]

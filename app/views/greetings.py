import discord

from app import logger
from app.components.embed import response_embed
from app.components.select import Select
from app.constants import Commands as commands_constants
from app.constants import LogTypes as logconstants
from app.constants import supported_locales
from app.integrations.google_translate import GoogleTranslate
from app.services.utils import ml, parse_locale

SETUP_FEATURES = [
    {
        "command_key": commands_constants.WELCOME_MESSAGES_KEY,
        "button_key": "welcome-messages",
        "emoji": "🎉",
    },
    {
        "command_key": commands_constants.DEFAULT_ROLES_KEY,
        "button_key": "default-roles",
        "emoji": "👩‍🎓",
    },
    {
        "command_key": commands_constants.NOTIFICATIONS_TWITCH_KEY,
        "button_key": "twitch",
        "emoji": "📡",
    },
    {
        "command_key": commands_constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        "button_key": "youtube",
        "emoji": "▶️",
    },
]


class SetupButton(discord.ui.Button):
    def __init__(self, command_key: str, label: str, emoji: str, locale: str, custom_id: str):
        self.command_key = command_key
        self.locale = locale
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.grey,
            custom_id=custom_id,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            embed = response_embed("buttons.setup.admin-only", self.locale)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        from app.services.moderations import send_command_form_message

        logger.info(
            f"command started ({interaction.id}): command {self.command_key} called by {interaction.user.id} in channel {interaction.channel.id} at guild {interaction.guild.id}",
            interaction=interaction,
            log_type=logconstants.COMMAND_CALL_TYPE,
        )

        await send_command_form_message(interaction, self.command_key)


class DashboardButton(discord.ui.Button):
    def __init__(self, label: str, locale: str):
        self.locale = locale
        super().__init__(
            label=label,
            emoji="🔧",
            style=discord.ButtonStyle.primary,
            custom_id="greetings:setup:dashboard",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            embed = response_embed("buttons.setup.admin-only", self.locale)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        from app.data.moderations import find_moderations_by_guild
        from app.views.setup import SetupView

        locale = parse_locale(interaction.locale)
        moderations = find_moderations_by_guild(interaction.guild.id) or {}
        view = SetupView(moderations, locale)
        embed = view.get_embed()

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


def _resolve_locale(guild: discord.Guild) -> str:
    locale = parse_locale(guild.preferred_locale)
    if locale not in supported_locales:
        return "en-us"
    return locale


def _build_setup_buttons(locale: str):
    buttons = []
    for feature in SETUP_FEATURES:
        label = ml(f"buttons.setup.{feature['button_key']}.label", locale)
        custom_id = f"greetings:setup:{feature['command_key']}"
        buttons.append(
            SetupButton(
                command_key=feature["command_key"],
                label=label,
                emoji=feature["emoji"],
                locale=locale,
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
        row=1,
    )


class GreetingsView(discord.ui.View):
    def __init__(self, locale: str = "en-us"):
        super().__init__(timeout=None)
        self.locale = locale
        self._build(locale)

    def _build(self, locale: str):
        for button in _build_setup_buttons(locale):
            self.add_item(button)

        dashboard_label = ml("buttons.setup.dashboard.label", locale)
        self.add_item(DashboardButton(label=dashboard_label, locale=locale))

        self.add_item(_build_language_select(locale, self.language_callback))

    async def send(self, guild: discord.Guild):
        locale = _resolve_locale(guild)
        self.locale = locale

        self.clear_items()
        self._build(locale)

        embed = response_embed(
            "messages.greetings-message",
            locale,
            image=True,
        )

        for channel in guild.text_channels:
            if not channel.permissions_for(guild.me).send_messages:
                continue
            return await channel.send(embed=embed, view=self)

    async def language_callback(self, interaction: discord.Interaction):
        selected = self.selected_options[0]
        self.locale = selected

        new_embed = response_embed(
            "messages.greetings-message",
            selected,
            image=True,
        )

        self.clear_items()
        self._build(selected)

        await interaction.response.edit_message(embed=new_embed, view=self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"GreetingsView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )

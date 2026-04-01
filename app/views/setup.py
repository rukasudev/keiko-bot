import discord

from app import logger
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons as icons
from app.constants import LogTypes as logconstants
from app.constants import Style as style
from app.services.utils import ml

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
        "command_key": commands_constants.BLOCK_LINKS_KEY,
        "button_key": None,
        "emoji": "🚫",
        "label_fallback": "Block Links",
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

FEATURE_COMMANDS = {
    commands_constants.WELCOME_MESSAGES_KEY: {
        "group": "moderations",
        "namespace": "welcome-messages",
    },
    commands_constants.DEFAULT_ROLES_KEY: {
        "group": "moderations",
        "namespace": "default-roles",
    },
    commands_constants.BLOCK_LINKS_KEY: {
        "group": "moderations",
        "namespace": "block-links",
    },
    commands_constants.NOTIFICATIONS_TWITCH_KEY: {
        "group": "notifications",
        "namespace": "notifications-twitch",
    },
    commands_constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: {
        "group": "notifications",
        "namespace": "notifications-youtube",
    },
}


def _get_translated_command(command_key: str, locale: str) -> str:
    info = FEATURE_COMMANDS[command_key]
    group = ml(f"commands.groups.{info['group']}", locale)
    subgroup = ml(f"commands.commands.{info['namespace']}.subgroup", locale)
    name = ml(f"commands.commands.{info['namespace']}.name", locale)
    return f"/{group} {subgroup} {name}"


class SetupFeatureButton(discord.ui.Button):
    def __init__(self, command_key: str, label: str, emoji: str, locale: str):
        self.command_key = command_key
        self.locale = locale
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.green,
        )

    async def callback(self, interaction: discord.Interaction):
        from app.services.moderations import send_command_form_message

        logger.info(
            f"command started ({interaction.id}): command {self.command_key} called by {interaction.user.id} in channel {interaction.channel.id} at guild {interaction.guild.id}",
            interaction=interaction,
            log_type=logconstants.COMMAND_CALL_TYPE,
        )

        await send_command_form_message(interaction, self.command_key)


class SetupView(discord.ui.View):
    def __init__(self, moderations: dict, locale: str):
        super().__init__(timeout=300)
        self.locale = locale
        self.moderations = moderations
        self._build(moderations, locale)

    def _build(self, moderations: dict, locale: str):
        for feature in SETUP_FEATURES:
            configured = moderations.get(feature["command_key"], False)

            if feature["button_key"]:
                label = ml(f"buttons.setup.{feature['button_key']}.label", locale)
            else:
                label = feature.get("label_fallback", feature["command_key"])

            if configured:
                configured_label = ml("commands.commands.setup.embed.configured", locale)
                button = discord.ui.Button(
                    label=f"✅ {label}",
                    emoji=feature["emoji"],
                    style=discord.ButtonStyle.grey,
                    disabled=True,
                )
                self.add_item(button)
            else:
                self.add_item(
                    SetupFeatureButton(
                        command_key=feature["command_key"],
                        label=label,
                        emoji=feature["emoji"],
                        locale=locale,
                    )
                )

    def get_embed(self) -> discord.Embed:
        locale = self.locale
        base = "commands.commands.setup.embed"

        title = ml(f"{base}.title", locale)
        description = ml(f"{base}.desc", locale)
        configured_text = ml(f"{base}.configured", locale)
        not_configured_text = ml(f"{base}.not-configured", locale)

        lines = []
        all_configured = True
        for feature in SETUP_FEATURES:
            is_configured = self.moderations.get(feature["command_key"], False)
            command = _get_translated_command(feature["command_key"], locale)

            if feature["button_key"]:
                name = ml(f"buttons.setup.{feature['button_key']}.label", locale)
            else:
                name = feature.get("label_fallback", feature["command_key"])

            status = f"✅ {configured_text}" if is_configured else f"❌ {not_configured_text}"
            lines.append(f"**{name}** — {status}\n`{command}`")
            if not is_configured:
                all_configured = False

        description += "\n\n" + "\n\n".join(lines)

        if all_configured:
            description += f"\n\n{ml(f'{base}.all-configured', locale)}"

        embed = discord.Embed(
            color=int(style.BACKGROUND_COLOR, base=16),
            title=title,
            description=description,
        )
        embed.set_thumbnail(url=icons.IMAGE_02)
        embed.set_footer(text=f"• Use the command /report to tell me a bug")

        return embed

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"SetupView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )

import discord

from app import logger
from app.constants import Commands as commands_constants
from app.constants import LogTypes as logconstants
from app.constants import Style as style
from app.data.cogs import find_cog_by_guild_id
from app.services.cache import increment_redis_key
from app.services.utils import get_command_by_key, ml

def _get_translated_command(command_key: str, locale: str) -> str:
    info = commands_constants.FEATURE_COMMANDS[command_key]
    group = ml(f"commands.groups.{info['group']}", locale)
    subgroup = ml(f"commands.commands.{info['namespace']}.subgroup", locale)
    name = ml(f"commands.commands.{info['namespace']}.name", locale)
    return f"/{group} {subgroup} {name}"


def _get_translated_group(command_key: str, locale: str) -> str:
    info = commands_constants.FEATURE_COMMANDS[command_key]
    return ml(f"commands.groups.{info['group']}", locale).capitalize()


class SetupFeatureButton(discord.ui.Button):
    def __init__(self, command_key: str, label: str, emoji: str, locale: str):
        self.command_key = command_key
        self.locale = locale
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction):
        import importlib

        from app.components.buttons import ExecuteCommandButton

        command = get_command_by_key(interaction.client, self.command_key)
        command_name = command.qualified_name if command else self.command_key

        logger.info(
            f"command started ({interaction.id}): command {command_name} called by {interaction.user.id} in channel {interaction.channel.id} at guild {interaction.guild.id}",
            interaction=interaction,
            log_type=logconstants.COMMAND_CALL_TYPE,
            command_name=command_name,
            interaction_source="button (setup)",
        )

        increment_redis_key(f"{logconstants.COMMAND_CALL_TYPE}:{self.command_key}:button")

        guild_id = str(interaction.guild.id)
        service = importlib.import_module(ExecuteCommandButton.COMMAND_SERVICES[self.command_key])
        await service.manager(interaction=interaction, guild_id=guild_id)


class SetupView(discord.ui.View):
    def __init__(self, moderations: dict, locale: str, guild_id: str = None):
        super().__init__(timeout=300)
        self.locale = locale
        self.moderations = moderations
        self.guild_id = guild_id
        self._build(moderations, locale)

    def _get_feature_status(self, command_key: str, is_configured: bool):
        if not is_configured:
            return "not-configured", "🔴"

        if self.guild_id:
            cog_data = find_cog_by_guild_id(self.guild_id, command_key)
            if cog_data and not cog_data.get(commands_constants.ENABLED_KEY, True):
                return "paused", "⏸"

        return "enabled", "🟢"

    def _build(self, moderations: dict, locale: str):
        for feature in commands_constants.SETUP_FEATURES:
            label = ml(f"buttons.setup.{feature['button_key']}.label", locale)
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

        embed = discord.Embed(
            color=int(style.BACKGROUND_COLOR, base=16),
            title=title,
            description=description,
        )

        all_configured = True
        for feature in commands_constants.SETUP_FEATURES:
            is_configured = self.moderations.get(feature["command_key"], False)
            status_key, status_emoji = self._get_feature_status(feature["command_key"], is_configured)
            command = _get_translated_command(feature["command_key"], locale)
            group = _get_translated_group(feature["command_key"], locale)
            name = ml(f"buttons.setup.{feature['button_key']}.label", locale)
            status_text = ml(f"{base}.{status_key}", locale)

            field_name = f"[ {group} ] {name} - {status_text} {status_emoji}"
            field_value = f"`{command}`"

            if not is_configured:
                all_configured = False

            embed.add_field(name=field_name, value=field_value, inline=False)

        if all_configured:
            embed.description += f"\n\n{ml(f'{base}.all-configured', locale)}"

        footer_text = ml(f"{base}.footer", locale)
        embed.set_footer(text=f"• {footer_text}")

        return embed

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"SetupView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )

"""The /setup card: every feature Keiko offers, its status, and a button to open it."""
import discord

from app import logger
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants as view_constants
from app.data.cogs import find_cog_by_guild_id_async
from app.data.moderations import find_moderations_by_guild_async
from app.forms.adapters.discord import layout
from app.services.utils import ml


def _translated_command(command_key: str, locale: str) -> str:
    info = commands_constants.FEATURE_COMMANDS[command_key]
    group = ml(f"commands.groups.{info['group']}", locale)
    subgroup = ml(f"commands.commands.{info['namespace']}.subgroup", locale)
    name = ml(f"commands.commands.{info['namespace']}.name", locale)
    return f"/{group} {subgroup} {name}"


async def _status(guild_id: str, command_key: str, moderations: dict) -> tuple:
    if not moderations.get(command_key, False):
        return "not-configured", "🔴"
    cog_data = await find_cog_by_guild_id_async(guild_id, command_key)
    if cog_data and not cog_data.get(commands_constants.ENABLED_KEY, True):
        return "paused", "⏸️"
    return "enabled", "🟢"


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


async def setup_dashboard(guild_id: str, locale: str) -> discord.ui.LayoutView:
    """The /setup card for this guild, in the admin's language."""
    base = "commands.commands.setup.embed"
    features = commands_constants.SETUP_FEATURES
    moderations = await find_moderations_by_guild_async(guild_id) or {}
    statuses = [await _status(guild_id, f["command_key"], moderations) for f in features]
    ready = sum(1 for status, _ in statuses if status != "not-configured")

    progress = ml(f"{base}.progress", locale)
    intro = [
        ml(f"{base}.desc", locale),
        progress.replace("{ready}", str(ready)).replace("{total}", str(len(features))),
    ]
    if ready == len(features):
        intro.append(ml(f"{base}.all-configured", locale))

    card = layout.container()
    layout.header(card, ml(f"{base}.title", locale), "\n\n".join(intro), KeikoIcons.IMAGE_01)
    for feature, (status, emoji) in zip(features, statuses):
        button_key = feature["button_key"]
        name = ml(f"buttons.setup.{button_key}.label", locale)
        description = ml(f"buttons.setup.{button_key}.desc", locale)
        status_text = ml(f"{base}.{status}", locale)
        command = _translated_command(feature["command_key"], locale)
        configured = status != "not-configured"
        label_key = "buttons.setup.manage.label" if configured else "buttons.setup.start.label"
        layout.row(
            card,
            f"{feature['emoji']} **{name}** · {emoji} {status_text}\n{description}\n`{command}`",
            SetupFeatureButton(feature["command_key"], ml(label_key, locale), configured),
        )
    layout.footer(card, f"• {ml(f'{base}.footer', locale)}")

    view = SetupView()
    view.add_item(card)
    return view

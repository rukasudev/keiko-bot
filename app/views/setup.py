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
    moderations = await find_moderations_by_guild_async(guild_id) or {}
    states = [
        (feature, await _state(guild_id, feature["command_key"], moderations))
        for feature in commands_constants.SETUP_FEATURES
    ]
    groups = (
        ("configured", [(f, s) for f, s in states if s != "pending"], "buttons.setup.manage.label"),
        ("pending", [(f, s) for f, s in states if s == "pending"], "buttons.setup.start.label"),
    )
    has_pending = bool(groups[1][1])

    card = layout.container()
    intro = ml(f"{base}.desc" if has_pending else f"{base}.all-configured", locale)
    layout.header(card, ml(f"{base}.title", locale), intro, KeikoIcons.IMAGE_01)
    for group_key, members, label_key in groups:
        if not members:
            continue
        title = ml(f"{base}.{group_key}", locale).replace("{count}", str(len(members)))
        layout.row(card, f"### {title}")
        for feature, state in members:
            button = SetupFeatureButton(
                feature["command_key"], ml(label_key, locale), group_key == "configured"
            )
            layout.row(card, _row_text(feature, state, locale), button, separated=False)
    layout.footer(card, f"• {ml(f'{base}.footer', locale)}")

    view = SetupView()
    view.add_item(card)
    return view

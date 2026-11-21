"""The /integrations StreamElements command."""

import discord

from app.bot import DiscordBot
from app.decorators import keiko_admin_only, keiko_command
from app.forms import open_feature
from app.translator import locale_str
from app.types.cogs import Group


class StreamElementsCommands(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str(
        "streamelements",
        type="subgroup",
        namespace="stream_elements_commands.command.slash",
    ),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str(
            "commands", type="name", namespace="stream_elements_commands.command.slash"
        ),
        description=locale_str(
            "desc",
            type="description",
            namespace="stream_elements_commands.command.slash",
        ),
    )
    @keiko_admin_only
    async def stream_elements_commands(self, interaction: discord.Interaction) -> None:
        """Open the setup form, or the manager of the StreamElements commands."""
        await open_feature(interaction, "stream_elements_commands")

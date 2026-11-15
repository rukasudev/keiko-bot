"""The /moderations welcome messages command."""

import discord

from app.bot import DiscordBot
from app.commands.welcome import service as welcome_messages_service
from app.decorators import keiko_admin_only, keiko_command
from app.translator import locale_str
from app.types.cogs import Group


class Welcome(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str("welcome", type="subgroup", namespace="welcome_messages.slash"),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("messages", type="name", namespace="welcome_messages.slash"),
        description=locale_str(
            "messages", type="description", namespace="welcome_messages.slash"
        ),
    )
    @keiko_admin_only
    async def welcome_messages(self, interaction: discord.Interaction) -> None:
        """Open the setup form, or the manager of the welcome messages."""
        await welcome_messages_service.manager(interaction)

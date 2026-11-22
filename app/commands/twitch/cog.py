"""The /notifications twitch lives command: the Twitch announcements of a server."""

import discord

from app.bot import DiscordBot
from app.decorators import keiko_admin_only, keiko_command
from app.forms import open_feature
from app.translator import locale_str
from app.types.cogs import Group


class Twitch(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str(
        "twitch", type="subgroup", namespace="notifications_twitch.command.slash"
    ),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str(
            "lives", type="name", namespace="notifications_twitch.command.slash"
        ),
        description=locale_str(
            "desc", type="description", namespace="notifications_twitch.command.slash"
        ),
    )
    @keiko_admin_only
    async def notifications_twitch(self, interaction: discord.Interaction) -> None:
        """Open the setup form, or the manager of the Twitch announcements."""
        await open_feature(interaction, "notifications_twitch")

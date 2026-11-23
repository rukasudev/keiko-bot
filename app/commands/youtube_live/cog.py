"""The /notifications youtube lives command, a leaf of the shared youtube subgroup."""

import discord

from app.bot import DiscordBot
from app.decorators import keiko_admin_only, keiko_command
from app.forms import open_feature
from app.translator import locale_str
from app.types.cogs import Group


class YoutubeLive(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str(
        "youtube",
        type="subgroup",
        namespace="notifications_youtube_live.command.slash",
    ),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str(
            "lives", type="name", namespace="notifications_youtube_live.command.slash"
        ),
        description=locale_str(
            "desc",
            type="description",
            namespace="notifications_youtube_live.command.slash",
        ),
    )
    @keiko_admin_only
    async def notifications_youtube_live(
        self, interaction: discord.Interaction
    ) -> None:
        """Open the setup form, or the manager of the YouTube live announcements."""
        await open_feature(interaction, "notifications_youtube_live")

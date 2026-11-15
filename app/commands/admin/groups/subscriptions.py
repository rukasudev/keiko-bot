"""/admin subscriptions: the Twitch subscriptions and the YouTube renewals."""

import discord

from app.bot import DiscordBot
from app.commands.admin.subscriptions import (
    get_reminder_subscriptions,
    get_twitch_subscriptions,
)
from app.decorators import keiko_command
from app.types.cogs import Group


class Subscriptions(Group, name="subscriptions"):  # noqa: D101 - discord.py sends a Group's docstring as its description
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name="twitch",
        description="Manage Twitch subscriptions",
    )
    async def show_twitch_subscriptions(self, interaction: discord.Interaction) -> None:
        """Send the Twitch subscriptions screen."""
        await interaction.response.defer(thinking=True)

        view = await get_twitch_subscriptions()
        await interaction.followup.send(
            view=view, embed=view.custom_embed, ephemeral=True
        )

    @keiko_command(
        name="reminder",
        description="Manage reminders subscriptions",
    )
    async def show_reminder_subscriptions(
        self, interaction: discord.Interaction
    ) -> None:
        """Send the YouTube renewals screen."""
        await interaction.response.defer(thinking=True)

        view = await get_reminder_subscriptions()
        await interaction.followup.send(
            view=view, embed=view.custom_embed, ephemeral=True
        )

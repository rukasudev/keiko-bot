"""/admin debug: Keiko's events triggered by hand."""

from typing import Optional

import discord

from app.bot import DiscordBot
from app.core.commands import MemberJoined, command_for
from app.decorators import keiko_command
from app.types.cogs import Group


class Debug(Group, name="debug"):  # noqa: D101 - discord.py sends a Group's docstring as its description
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name="test-welcome",
        description="Trigger a welcome message for testing purposes",
    )
    async def test_welcome_message(
        self, interaction: discord.Interaction, member: Optional[discord.Member] = None
    ) -> None:
        """Send the welcome message of `member`, the caller by default."""
        target = member or interaction.user
        await interaction.response.defer(ephemeral=True)

        greet = command_for("welcome_messages").hooks.member_joined
        try:
            if greet is None:
                raise LookupError("welcome messages greet no member")
            await greet(MemberJoined(str(interaction.guild_id), str(target.id), target))
            await interaction.followup.send(
                ":white_check_mark: Welcome message sent for "
                f"**{target.display_name}**!",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f":x: Failed to send welcome message: `{e}`", ephemeral=True
            )

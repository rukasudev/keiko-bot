"""/admin sync: the command tree synced with Discord."""

import discord

from app.bot import DiscordBot
from app.decorators import keiko_command
from app.types.cogs import Group


class Sync(Group, name="sync"):  # noqa: D101 - discord.py sends a Group's docstring as its description
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name="guild",
        description=(
            "Keiko has synchronized the command tree specifically for this guild"
        ),
    )
    async def sync_guild(self, interaction: discord.Interaction) -> None:
        """Sync the command tree of this guild."""
        await interaction.response.defer()

        await self.bot.tree.sync(guild=interaction.guild)
        message = "Everything is now up-to-date and ready to use!"

        await interaction.followup.send(message)

    @keiko_command(
        name="global",
        description="Keiko has synchronized the global command tree for all guilds",
    )
    async def sync_global(self, interaction: discord.Interaction) -> None:
        """Sync the global command tree."""
        await interaction.response.defer()

        await self.bot.tree.sync()
        message = (
            "Now, every corner of Keiko's world is in harmony and ready "
            "for your commands!"
        )

        await interaction.followup.send(message)

"""The /moderations roles auto command."""

import discord

from app.bot import DiscordBot
from app.decorators import keiko_admin_only, keiko_command
from app.forms import open_feature
from app.translator import locale_str
from app.types.cogs import Group


class Roles(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str("roles", type="subgroup", namespace="default_roles.command.slash"),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("auto", type="name", namespace="default_roles.command.slash"),
        description=locale_str(
            "desc", type="description", namespace="default_roles.command.slash"
        ),
    )
    @keiko_admin_only
    async def default_roles(self, interaction: discord.Interaction) -> None:
        """Open the setup form, or the manager of the auto roles."""
        await open_feature(interaction, "default_roles")

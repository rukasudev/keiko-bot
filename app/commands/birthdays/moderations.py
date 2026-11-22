"""/moderations birthdays: the birthday reminders of a server and the members' dates."""

import discord

from app.bot import DiscordBot
from app.decorators import keiko_admin_only, keiko_command
from app.forms import open_feature
from app.translator import locale_str
from app.types.cogs import Group


class Birthdays(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str(
        "birthdays", type="subgroup", namespace="reminders_birthday.command.slash"
    ),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str(
            "reminders", type="name", namespace="reminders_birthday.command.slash"
        ),
        description=locale_str(
            "desc", type="description", namespace="reminders_birthday.command.slash"
        ),
    )
    @keiko_admin_only
    async def reminders(self, interaction: discord.Interaction) -> None:
        """Open the setup form, or the manager of the birthday reminders."""
        await open_feature(interaction, "reminders_birthday")

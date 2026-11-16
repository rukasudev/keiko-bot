"""/moderations birthdays: the birthday reminders of a server and the members' dates."""

import discord
from discord import app_commands

from app.bot import DiscordBot
from app.commands.birthdays import service as birthdays_service
from app.constants import KeikoIcons
from app.core.calendar import parse_date_parts
from app.core.dates import format_mm_dd_label, get_month_choices
from app.decorators import keiko_admin_only, keiko_command
from app.translator import locale_str
from app.types.cogs import Group
from app.ui.embed import response_embed, response_error_embed


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
        await birthdays_service.manager(interaction)

    @keiko_command(
        name=locale_str(
            "add",
            type="name",
            namespace="reminders_birthday.command.slash.commands.add",
        ),
        description=locale_str(
            "desc",
            type="description",
            namespace="reminders_birthday.command.slash.commands.add",
        ),
    )
    @app_commands.rename(
        member=locale_str(
            "member",
            type="params.member.name",
            namespace="reminders_birthday.command.slash",
        ),
        month=locale_str(
            "month",
            type="params.month.name",
            namespace="reminders_birthday.command.slash",
        ),
        day=locale_str(
            "day", type="params.day.name", namespace="reminders_birthday.command.slash"
        ),
    )
    @app_commands.describe(
        member=locale_str(
            "member-desc",
            type="params.member.description",
            namespace="reminders_birthday.command.slash",
        ),
        month=locale_str(
            "month-desc",
            type="params.month.description",
            namespace="reminders_birthday.command.slash",
        ),
        day=locale_str(
            "day-desc",
            type="params.day.description",
            namespace="reminders_birthday.command.slash",
        ),
    )
    @app_commands.choices(month=get_month_choices())
    @keiko_admin_only
    async def add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        month: int,
        day: int,
    ) -> None:
        """Save a member's birthday for them."""
        date = parse_date_parts(day, month)
        if not date:
            embed = response_error_embed(
                "invalid_date", interaction.locale, footer=True
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        if not await birthdays_service.accepts_birthdays(guild_id):
            embed = response_error_embed(
                "reminders_birthday.ui.errors.disabled", interaction.locale, footer=True
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        await birthdays_service.upsert_birthday(
            guild_id=guild_id, user_id=str(member.id), mm_dd=date
        )

        embed = response_embed(
            "reminders_birthday.ui.responses.add",
            interaction.locale,
            facts=birthdays_service.command_facts(
                member=member.mention,
                date=format_mm_dd_label(date, interaction.locale),
            ),
            thumbnail=KeikoIcons.IMAGE_03,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

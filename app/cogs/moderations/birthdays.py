import discord
from discord import app_commands

from app.bot import DiscordBot
from app.components.embed import response_embed, response_error_embed
from app.constants import KeikoIcons
from app.data import birthdays as birthdays_data
from app.decorators import keiko_admin_only, keiko_command
from app.services import birthdays as birthdays_service
from app.services.birthdays import format_birthday_date_value
from app.services.dates import get_month_choices
from app.translator import locale_str
from app.types.cogs import Group


class Birthdays(
    Group,
    name=locale_str("birthdays", type="subgroup", namespace="moderations-birthdays"),
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("reminders", type="name", namespace="moderations-birthdays"),
        description=locale_str("desc", type="desc", namespace="moderations-birthdays"),
    )
    @keiko_admin_only
    async def reminders(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild.id)
        await birthdays_service.manager(interaction=interaction, guild_id=guild_id)

    @keiko_command(
        name=locale_str("add-name", type="add-name", namespace="moderations-birthdays"),
        description=locale_str("add-desc", type="add-desc", namespace="moderations-birthdays"),
    )
    @app_commands.rename(
        member=locale_str("member", type="params.member", namespace="moderations-birthdays"),
        month=locale_str("month", type="params.month", namespace="moderations-birthdays"),
        day=locale_str("day", type="params.day", namespace="moderations-birthdays"),
    )
    @app_commands.describe(
        member=locale_str("member-desc", type="params.member-desc", namespace="moderations-birthdays"),
        month=locale_str("month-desc", type="params.month-desc", namespace="moderations-birthdays"),
        day=locale_str("day-desc", type="params.day-desc", namespace="moderations-birthdays"),
    )
    @app_commands.choices(month=get_month_choices())
    @keiko_admin_only
    async def add(self, interaction: discord.Interaction, member: discord.Member, month: int, day: int) -> None:
        date = birthdays_service.parse_birthday_date_parts(day, month)
        if not date:
            embed = response_error_embed("birthday-invalid-date", interaction.locale, footer=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        if not birthdays_data.is_birthday_enabled(guild_id) or not birthdays_data.find_birthday_config(guild_id):
            embed = response_error_embed("birthday-cog-disabled", interaction.locale, footer=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        birthdays_service.upsert_self_birthday(
            guild_id=guild_id,
            user_id=str(member.id),
            mm_dd=date,
            increment_self_edit=False,
        )

        embed = response_embed(
            "commands.commands.moderations-birthdays.add-response",
            interaction.locale,
            footer=True,
            image=True,
        )
        embed.set_thumbnail(url=KeikoIcons.IMAGE_03)
        embed.description = (
            embed.description
            .replace("{member}", member.mention)
            .replace("{date}", format_birthday_date_value(date, interaction.locale))
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

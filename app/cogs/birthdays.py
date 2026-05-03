import discord
from discord import app_commands

from app.bot import DiscordBot
from app.components.embed import response_embed, response_error_embed
from app.constants import KeikoIcons
from app.data import birthdays as birthdays_data
from app.decorators import keiko_command
from app.services import birthdays as birthdays_service
from app.services.birthdays import format_birthday_date_value
from app.services.dates import get_month_choices
from app.services.utils import ml
from app.translator import locale_str
from app.types.cogs import Cog


class BirthdayOverwriteConfirmView(discord.ui.View):
    def __init__(self, guild_id: str, user_id: str, mm_dd: str, locale: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.mm_dd = mm_dd
        self.locale = locale
        self.confirm.label = ml("buttons.confirm.label", locale=locale) or "Confirm"
        self.cancel.label = ml("buttons.cancel.label", locale=locale) or "Cancel"

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        birthdays_service.upsert_self_birthday(
            guild_id=self.guild_id,
            user_id=self.user_id,
            mm_dd=self.mm_dd,
            increment_self_edit=True,
        )
        embed = response_embed(
            "commands.commands.birthday-personal.overwrite-response",
            self.locale,
            footer=True,
            image=True,
        )
        embed.set_thumbnail(url=KeikoIcons.IMAGE_03)
        embed.description = embed.description.replace(
            "{date}",
            format_birthday_date_value(self.mm_dd, self.locale),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(view=None)
        self.stop()


@app_commands.guild_only()
class Birthday(Cog, name=locale_str("birthday", type="name", namespace="birthday-personal")):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("birthday", type="name", namespace="birthday-personal"),
        description=locale_str("desc", type="desc", namespace="birthday-personal"),
    )
    @app_commands.rename(
        month=locale_str("month", type="birthday-params.month.name", namespace="commons"),
        day=locale_str("day", type="birthday-params.day.name", namespace="commons"),
    )
    @app_commands.describe(
        month=locale_str("month-desc", type="birthday-params.month.desc", namespace="commons"),
        day=locale_str("day-desc", type="birthday-params.day.desc", namespace="commons"),
    )
    @app_commands.choices(month=get_month_choices())
    async def birthday_personal(self, interaction: discord.Interaction, month: int, day: int) -> None:
        date = birthdays_service.parse_birthday_date_parts(day, month)
        if not date:
            embed = response_error_embed("invalid-date", interaction.locale, footer=True)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        guild_id = str(interaction.guild.id)
        if not birthdays_data.is_birthday_enabled(guild_id) or not birthdays_data.find_birthday_config(guild_id):
            embed = response_error_embed("reminders-birthdays-disabled", interaction.locale, footer=True)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        existing = birthdays_data.find_birthday_item(guild_id, str(interaction.user.id))
        if existing:
            if not birthdays_service.can_self_edit_birthday(existing):
                embed = response_error_embed("reminders-birthdays-self-edit-limit", interaction.locale, footer=True)
                return await interaction.followup.send(embed=embed, ephemeral=True)

            current_date = existing.get("date")
            current_date_text = format_birthday_date_value(current_date, interaction.locale) or "-"
            embed = response_embed(
                "commands.commands.birthday-personal.overwrite-confirm",
                interaction.locale,
                footer=True,
                image=True,
            )
            embed.description = (
                embed.description
                .replace("{current_date}", current_date_text)
                .replace("{new_date}", format_birthday_date_value(date, interaction.locale))
            )
            view = BirthdayOverwriteConfirmView(
                guild_id=guild_id,
                user_id=str(interaction.user.id),
                mm_dd=date,
                locale=interaction.locale,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return

        birthdays_service.upsert_self_birthday(
            guild_id=guild_id,
            user_id=str(interaction.user.id),
            mm_dd=date,
        )

        embed = response_embed(
            "commands.commands.birthday-personal.response",
            interaction.locale,
            footer=True,
            image=True,
        )
        embed.set_thumbnail(url=KeikoIcons.IMAGE_03)
        embed.description = embed.description.replace(
            "{date}",
            format_birthday_date_value(date, interaction.locale),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Birthday(bot))

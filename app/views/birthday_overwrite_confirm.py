import discord

from app.components.embed import response_embed
from app.constants import KeikoIcons
from app.services import reminders_birthdays as birthdays_service
from app.services.reminders_birthdays import format_birthday_date_value
from app.services.utils import ml


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

import discord


class BirthdayEditChoiceView(discord.ui.View):
    def __init__(self, manager_view: discord.ui.View):
        super().__init__(timeout=1800)
        self.manager_view = manager_view
        self.locale = manager_view.locale

    @discord.ui.button(label="Canal", emoji="📺", style=discord.ButtonStyle.grey)
    async def edit_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from app.services.reminders_birthdays import _open_birthday_config_form

        await _open_birthday_config_form(interaction, self.manager_view, ["channel"])

    @discord.ui.button(label="@everyone", emoji="📣", style=discord.ButtonStyle.grey)
    async def edit_everyone(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from app.services.reminders_birthdays import _open_birthday_config_form

        await _open_birthday_config_form(interaction, self.manager_view, ["mention_everyone"])

    @discord.ui.button(label="Membro", emoji="👤", style=discord.ButtonStyle.grey)
    async def edit_member(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from app.components.select_views import UserSelectView
        from app.services.reminders_birthdays import _manager_action_embed, _open_member_birthday_form, _t
        from app.services.utils import parse_locale

        embed = _manager_action_embed(
            parse_locale(interaction.locale),
            _t(self.locale, "Edit member birthday", "Editar aniversário de membro"),
            _t(self.locale, "Choose the member whose birthday you want to edit.", "Escolha o membro que terá o aniversário editado."),
        )
        view = UserSelectView(
            callback=lambda i: _open_member_birthday_form(i, view, self.locale),
            locale=self.locale,
            required=True,
            unique=True,
        )
        await interaction.response.edit_message(embed=embed, view=view)

import discord
from typing import List, Dict
from app.views.form import Form
from app.services.moderations import upsert_cog_by_guild
from app.services.utils import parse_command_event_description
from app.components.embed import parse_dict_to_embed
from i18n import t

# TODO: parse this to be a view with select instead discord.ui.Select
class EditCommand(discord.ui.Select):
    def __init__(self, command_key: str, locale: str, options: Dict[str, str]):
        self.command_key = command_key
        self.locale = locale
        self.parsed_options = self.parse_options(options)
        super().__init__(
            placeholder=t("commands.command-event.edit.placeholder", locale=self.locale),
            options=self.parsed_options, max_values=len(self.parsed_options)
        )

    def parse_options(self, options_dict: Dict[str, str]) -> List[discord.SelectOption]:
        options = []
        for label, value in options_dict.items():
            option = discord.SelectOption(label=label, value=value)
            options.append(option)

        return options

    async def callback(self, interaction: discord.Interaction):
        self.selected_options = self.values

        self.form_view = Form(self.command_key, self.locale, self.update_command)
        self.form_view.filter_questions(self.selected_options)

        return await self.form_view._callback(interaction)

    async def update_command(self, interaction: discord.Interaction):
        data = self.form_view._parse_responses_to_cog()

        await upsert_cog_by_guild(interaction.guild_id, self.command_key, data)

        question = list(self.form_view._get_questions())[0]
        embed = parse_dict_to_embed(question)

        embed.title = t("commands.command-event.edit.title", locale=self.locale)
        embed.description = parse_command_event_description(
            t("commands.command-event.edit.description", locale=self.locale),
            interaction.message.edited_at,
            interaction.message.interaction.name,
            interaction.user.mention
        )

        view = self.form_view.view
        view.clear_items()

        await interaction.response.edit_message(embed=embed, view=view)

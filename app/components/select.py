import discord
from typing import List, Dict
from app.views.form import Form
from app.services.moderations import upsert_cog_by_guild

# TODO: parse this to be a view with select instead discord.ui.Select
class EditCommand(discord.ui.Select):
    def __init__(self, command_key: str, locale: str, options: Dict[str, str]):
        self.command_key = command_key
        self.locale = locale
        self.parsed_options = self.parse_options(options)
        #TODO: replace this placeholder to use multilang
        super().__init__(
            placeholder="Select a command to edit", options=self.parsed_options
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

        question = list(self.form_view.questions)[0]
        self.form_view.set_question_embed(question)
        self.form_view.set_question(question)

        return await self.form_view.get_action_by_type(question["action"], interaction)

    async def update_command(self, interaction: discord.Interaction):
        response = self.form_view.view.get_response()

        if not isinstance(response, str):
            response = list(response)

        data = {self.selected_options[0]: response}
        await upsert_cog_by_guild(interaction.guild_id, self.command_key, data)

        embed = interaction.message.embeds[0]
        embed.title = "Commando editado com sucesso!"

        view = self.form_view.view
        view.clear_items()

        await interaction.response.edit_message(embed=embed, view=view)

from typing import Dict, List

import discord

from app.components.buttons import BackButton
from app.services.utils import ml


class Select(discord.ui.Select):
    def __init__(self, placeholder: str, options: Dict[str, str], custom_callback=None):
        self.parsed_options = self.parse_options(options)
        self.custom_callback = custom_callback
        super().__init__(
            placeholder=placeholder,
            options=self.parsed_options,
            max_values=len(self.parsed_options),
        )

    def parse_options(self, options_dict: Dict[str, str]) -> List[discord.SelectOption]:
        options = []

        if not options_dict:
            return options

        for key, label in options_dict.items():
            option = discord.SelectOption(label=label, value=key)
            options.append(option)

        return options

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_options = self.values
        if self.custom_callback:
            await self.custom_callback(interaction)
        else:
            await self.view.callback(interaction)


class HelpSelect(Select):
    def __init__(self, placeholder: str, data: Dict[str, List[str]]):
        self.data = data
        super().__init__(
            placeholder=placeholder,
            options=[],
            custom_callback=self.after_callback,
        )

    def get_data(self):
        data = {}
        for item in self.view.get_current_page_data():
            for command in self.data[item]:
                data[command["name"]] = command["name"]
        return data

    def get_data_with_desc(self):
        data = {}
        for item in self.view.get_current_page_data():
            for command in self.data[item]:
                data[command["name"]] = command["description"]
        return data

    def update(self):
        data = self.get_data()

        self.view.remove_item(self.view.select)
        self.view.select.options = self.view.select.parse_options(data)
        self.view.select.max_values = len(data)
        self.view.add_item_first(self.view.select)

    async def after_callback(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]

        self.view.embed._fields = []
        self.view.embed._footer = {}

        self.view.embed.title = ml(
            "commands.commands.help.embed-desc.title", interaction.locale
        )
        self.view.embed.description = ml(
            "commands.commands.help.embed-desc.desc", interaction.locale
        )

        data = self.get_data_with_desc()

        for key, item in data.items():
            if key not in self.view.selected_options:
                continue

            self.view.embed.add_field(
                name=f"✨ /{key}",
                value=item,
                inline=False,
            )

        new_view = discord.ui.View()
        new_view.add_item(
            BackButton(embed=embed, view=self.view, locale=interaction.locale)
        )

        await interaction.response.edit_message(embed=self.view.embed, view=new_view)

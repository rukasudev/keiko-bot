from typing import Dict, List

import discord

from app.components.buttons import BackButtom
from app.components.select import Select
from app.constants import KeikoIcons as icons_constants
from app.constants import Style as constants
from app.services.utils import ml


class PaginationView(discord.ui.View):
    def __init__(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        footer: str,
        data: List[Dict[str, str]],
        current_page: int = 1,
        sep: int = 2,
    ):
        self.title = title
        self.description = description
        self.footer = footer
        self.interaction = interaction
        self.current_page = current_page
        self.sep = sep

        self.separated_data = [data[i : i + sep] for i in range(0, len(data), sep)]

        super().__init__()

    async def send(self):
        await self.interaction.response.send_message(view=self)
        await self.update_message(self.get_current_page_data())

    def create_embed(self, data):
        self.embed = discord.Embed(
            title=self.title,
            description=self.description,
            color=int(constants.BACKGROUND_COLOR, base=16),
        )
        for item in data:
            self.embed.add_field(name=item["label"], value=item["item"], inline=False)
        self.embed.set_thumbnail(url=icons_constants.IMAGE_02)
        self.embed.set_footer(
            text=f"• {self.footer} {self.current_page} / {len(self.separated_data)}"
        )
        return self.embed

    def add_select(self, placeholder: str, first: bool = False):
        data = self.get_select_options()
        self.select = Select(placeholder, data, custom_callback=self.select_callback)

        if not first:
            return self.add_item(self.select)

        self.add_item_first(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]

        self.embed._fields = []
        self.embed._footer = {}

        self.embed.title = ml(
            "commands.commands.help.embed-desc.title", interaction.locale
        )
        self.embed.description = ml(
            "commands.commands.help.embed-desc.desc", interaction.locale
        )

        data = self.get_select_options_with_description()

        for key, item in data.items():
            if key not in self.selected_options:
                continue

            self.embed.add_field(
                name=f"✨ /{key}",
                value=item,
                inline=False,
            )

        new_view = discord.ui.View()
        new_view.add_item(BackButtom(embed=embed, view=self, locale=interaction.locale))

        await interaction.response.edit_message(embed=self.embed, view=new_view)

    def add_item_first(self, item):
        buttons = self.children
        self.clear_items()
        self.add_item(item)

        for button in buttons:
            self.add_item(button)

    def update_select(self):
        if not hasattr(self, "select"):
            return

        data = self.get_select_options()
        self.remove_item(self.select)
        self.select.options = self.select.parse_options(data)
        self.select.max_values = len(data)
        self.add_item_first(self.select)

    def get_select_options(self):
        data = {}
        for item in self.get_current_page_data():
            for command in item["commands"]:
                data[command["name"]] = command["name"]
        return data

    def get_select_options_with_description(self):
        data = {}
        for item in self.get_current_page_data():
            for command in item["commands"]:
                data[command["name"]] = command["description"]
        return data

    async def update_message(self, data):
        self.update_buttons()
        self.update_select()
        await self.interaction.edit_original_response(
            embed=self.create_embed(data), view=self
        )

    def update_buttons(self):
        total_pages = len(self.separated_data)
        self.first_page_button.disabled = self.current_page == 1
        self.prev_button.disabled = self.current_page == 1
        self.next_button.disabled = self.current_page == total_pages
        self.last_page_button.disabled = self.current_page == total_pages

    def get_current_page_data(self):
        if self.current_page - 1 < len(self.separated_data):
            return self.separated_data[self.current_page - 1]
        else:
            return []

    @discord.ui.button(label="|<", style=discord.ButtonStyle.green)
    async def first_page_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        self.current_page = 1
        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label="<", style=discord.ButtonStyle.primary)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        self.current_page -= 1
        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label=">", style=discord.ButtonStyle.primary)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        self.current_page += 1
        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label=">|", style=discord.ButtonStyle.green)
    async def last_page_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        self.current_page = len(self.separated_data)
        await self.update_message(self.get_current_page_data())

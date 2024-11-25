from typing import Dict, List

import discord

from app.constants import KeikoIcons as icons_constants
from app.constants import Style as constants
from app.services.utils import ml


class PaginationView(discord.ui.View):
    def __init__(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        data: List[Dict[str, str]],
        sep: int = 2,
        current_page: int = 1,
    ):
        self.title = title
        self.description = description
        self.interaction = interaction
        self.current_page = current_page
        self.sep = sep

        self.data = data
        self.separated_data = [
            list(data)[i : i + sep] for i in range(0, len(list(data)), sep)
        ]

        super().__init__(timeout=1800)

    async def send(self, ephemeral: bool = False):
        if hasattr(self, "select"):
            self.select.update()

        await self.interaction.response.send_message(view=self, ephemeral=ephemeral)
        await self.update_message(self.get_current_page_data())

    def create_embed(self, data):
        self.embed = discord.Embed(
            title=self.title,
            description=self.description,
            color=int(constants.BACKGROUND_COLOR, base=16),
        )

        for item in data:
            self.embed.add_field(name=item, value=self.data[item], inline=False)

        footer = ml("commands.pagination-view.footer", locale=self.interaction.locale)
        self.embed.set_thumbnail(url=icons_constants.IMAGE_02)
        self.embed.set_footer(
            text=f"• {footer} {self.current_page} / {len(self.separated_data)}"
        )
        return self.embed

    def add_select(
        self,
        select: discord.ui.Select,
        first: bool = False,
    ):
        self.select = select

        if not first:
            return self.add_item(self.select)

        self.add_item_first(self.select)

    def add_item_first(self, item):
        buttons = self.children
        self.clear_items()
        self.add_item(item)

        for button in buttons:
            self.add_item(button)

    async def update_message(self, data):
        if hasattr(self, "select"):
            self.select.update()

        self.update_buttons()
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

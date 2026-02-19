from typing import Dict, List

import discord

from app import logger
from app.constants import KeikoIcons as icons_constants
from app.constants import LogTypes as logconstants
from app.constants import Style as constants
from app.services.utils import ml


class PaginationWithoutInteractionView(discord.ui.View):
    def __init__(
        self,
        title: str,
        description: str,
        data: List[Dict[str, str]],
        message: discord.Message = None,
        thumbnail: str = icons_constants.IMAGE_02,
        sep: int = 2,
        current_page: int = 1,
    ):
        self.title = title
        self.description = description
        self.thumbnail = thumbnail
        self.current_page = current_page
        self.sep = sep
        self.message = message

        self.data = data
        self.separated_data = [
            list(data)[i : i + sep] for i in range(0, len(list(data)), sep)
        ]

        super().__init__(timeout=1800)

    async def send(self, message: discord.Message):
        if hasattr(self, "select"):
            self.select.update()

        self.message = await message.reply(embed=self.create_embed(self.get_current_page_data()), view=self)

    def create_embed(self, data):
        embed = discord.Embed(
            title=self.title,
            description=self.description,
            color=int(constants.BACKGROUND_COLOR, base=16),
        )

        for item in data:
            embed.add_field(name=item, value=self.data[item], inline=False)

        footer = ml("commands.pagination-view.footer", locale="en")
        embed.set_thumbnail(url=self.thumbnail)
        embed.set_footer(
            text=f"• {footer} {self.current_page} / {len(self.separated_data)}"
        )
        return embed

    async def update_message(self):
        if self.message:
            if hasattr(self, "select"):
                self.select.update()

            self.update_buttons()
            await self.message.edit(embed=self.create_embed(self.get_current_page_data()), view=self)

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
    async def first_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_page = 1
        await self.update_message()

    @discord.ui.button(label="<", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_page -= 1
        await self.update_message()

    @discord.ui.button(label=">", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_page += 1
        await self.update_message()

    @discord.ui.button(label=">|", style=discord.ButtonStyle.green)
    async def last_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_page = len(self.separated_data)
        await self.update_message()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"PaginationWithoutInteractionView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )

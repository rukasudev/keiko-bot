"""A paginated embed of fields, with arrows and a select that may follow the page."""

from collections.abc import Mapping, Sequence
from typing import Any, Optional

import discord

from app import logger
from app.constants import KeikoIcons as icons_constants
from app.constants import LogTypes as logconstants
from app.constants import Style as constants
from app.constants import ViewConstants as view_constants
from app.core.utils import ml
from app.ui.select import Select


class PageSelect(Select["PaginationView"]):
    """A select on a pagination view whose options follow the page on screen."""

    def follow(self, keys: Sequence[str]) -> None:
        """Offer the options of the page whose field names are `keys`."""
        raise NotImplementedError


class PaginationView(discord.ui.View):
    """Fields shown `sep` per page, under one title, with arrows between pages: the
    answer to an interaction (`send`), or the reply to a chat message (`reply`)."""

    def __init__(
        self,
        interaction: Optional[discord.Interaction],
        title: str,
        description: str,
        data: Mapping[str, str],
        sep: int = 2,
        current_page: int = 1,
        *,
        thumbnail: Optional[str] = icons_constants.IMAGE_02,
        locale: str = "",
    ) -> None:
        self.title = title
        self.description = description
        self.interaction = interaction
        self.current_page = current_page
        self.sep = sep
        self.select: Optional[PageSelect] = None
        self.thumbnail = thumbnail
        self.locale = locale
        self.message: Optional[discord.Message] = None

        self.data = data
        self.separated_data = [
            list(data)[i : i + sep] for i in range(0, len(list(data)), sep)
        ]

        super().__init__(timeout=view_constants.LONG_TIMEOUT_SECONDS)

    async def send(self, ephemeral: bool = False) -> None:
        """Answer the interaction with the first page."""
        self._follow_page()

        await self._answered().response.send_message(view=self, ephemeral=ephemeral)
        await self.update_message(self.get_current_page_data())

    async def reply(self, message: discord.Message) -> None:
        """Reply to a chat message with the first page; each arrow edits that reply."""
        self._follow_page()
        self.update_buttons()
        self.message = await message.reply(
            embed=self.create_embed(self.get_current_page_data()), view=self
        )

    def create_embed(self, data: Sequence[str]) -> discord.Embed:
        """The embed of the page whose field names are `data`."""
        self.embed = discord.Embed(
            title=self.title,
            description=self.description,
            color=int(constants.BACKGROUND_COLOR, base=16),
        )

        for item in data:
            self.embed.add_field(name=item, value=self.data[item], inline=False)

        locale = self.locale or self._answered().locale
        footer = ml("texts.page", locale=locale)
        self.embed.set_thumbnail(url=self.thumbnail)
        self.embed.set_footer(
            text=f"• {footer} {self.current_page} / {len(self.separated_data)}"
        )
        return self.embed

    def describe(self, title: str, description: str) -> discord.Embed:
        """The page's embed emptied of its fields and footer, under a new title."""
        self.embed.clear_fields()
        self.embed.remove_footer()
        self.embed.title = title
        self.embed.description = description
        return self.embed

    def add_select(self, select: PageSelect, first: bool = False) -> None:
        """Add the select that follows the page, above the arrows when `first`."""
        self.select = select

        if not first:
            self.add_item(self.select)
            return

        self.add_item_first(self.select)

    def add_item_first(self, item: discord.ui.Item[Any]) -> None:
        """Add `item` before every item already on the view."""
        buttons = self.children
        self.clear_items()
        self.add_item(item)

        for button in buttons:
            self.add_item(button)

    async def update_message(self, data: Sequence[str]) -> None:
        """Show the page whose field names are `data`."""
        self._follow_page()

        self.update_buttons()
        embed = self.create_embed(data)
        if self.message is not None:
            await self.message.edit(embed=embed, view=self)
            return
        await self._answered().edit_original_response(embed=embed, view=self)

    def update_buttons(self) -> None:
        """Disable the arrows that lead past the first or the last page."""
        total_pages = len(self.separated_data)
        self.first_page_button.disabled = self.current_page == 1
        self.prev_button.disabled = self.current_page == 1
        self.next_button.disabled = self.current_page == total_pages
        self.last_page_button.disabled = self.current_page == total_pages

    def get_current_page_data(self) -> list[str]:
        """The field names of the page on screen."""
        if self.current_page - 1 < len(self.separated_data):
            return self.separated_data[self.current_page - 1]
        return []

    def _follow_page(self) -> None:
        if self.select is not None:
            self.select.follow(self.get_current_page_data())

    def _answered(self) -> discord.Interaction:
        if self.interaction is None:
            raise RuntimeError("a page with no interaction replies to a chat message")
        return self.interaction

    @discord.ui.button(label="|<", style=discord.ButtonStyle.green)
    async def first_page_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button[Any]
    ) -> None:
        """Go to the first page."""
        await interaction.response.defer()
        self.current_page = 1
        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label="<", style=discord.ButtonStyle.primary)
    async def prev_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button[Any]
    ) -> None:
        """Go to the page before."""
        await interaction.response.defer()
        self.current_page -= 1
        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label=">", style=discord.ButtonStyle.primary)
    async def next_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button[Any]
    ) -> None:
        """Go to the page after."""
        await interaction.response.defer()
        self.current_page += 1
        await self.update_message(self.get_current_page_data())

    @discord.ui.button(label=">|", style=discord.ButtonStyle.green)
    async def last_page_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button[Any]
    ) -> None:
        """Go to the last page."""
        await interaction.response.defer()
        self.current_page = len(self.separated_data)
        await self.update_message(self.get_current_page_data())

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item[Any],
    ) -> None:
        """Log what a page turn raised."""
        logger.error(
            f"PaginationView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )

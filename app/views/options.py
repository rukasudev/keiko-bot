from typing import Callable, Dict, List, Union

import discord

from app import logger
from app.components.buttons import (
    CancelButton,
    ConfirmButton,
    OptionsButton,
    PaginationButton,
)
from app.components.embed import response_error_embed
from app.constants import LogTypes as logconstants


class OptionsView(discord.ui.View):
    def __init__(
        self,
        options: Union[List[str], Dict[str, str]],
        callback: Callable,
        locale: str,
        styled_values: bool = False,
        required: bool = False,
        unique: bool = False,
    ) -> None:
        super().__init__(timeout=1800)
        self.callback = callback
        self.locale = locale
        self.required = required
        self.unique = unique
        self.response = {}
        self.styled_values = styled_values
        self.options = options
        self.items_per_page = 20
        self.current_page = 0
        self.max_pages = (len(options) // self.items_per_page) + (0 if len(options) % self.items_per_page == 0 else 1)
        self.set_options(options)
        self.add_buttons()

    def set_options(self, options):
        options = list(options.items() if self.styled_values else options)

        if len(options) >= 24:
            page_start = self.current_page * self.items_per_page
            page_end = page_start + self.items_per_page
            options = options[page_start:page_end]

        for index, option in enumerate(options):
            key, value = (option[0], option[1]) if self.styled_values else (option, index)
            option_button = OptionsButton(
                options_custom_id=str(value), options_label=str(key), unique=self.unique, checked=str(value) in self.response
            )
            self.add_item(option_button)

    def add_buttons(self):
        if len(self.options) >= 24:
            self.add_pagination_buttons()
        self.add_item(ConfirmButton(callback=self._confirm_callback, locale=self.locale))
        self.add_item(CancelButton(locale=self.locale))

    def add_pagination_buttons(self):
        prev_disabled = self.current_page == 0
        next_disabled = self.current_page == self.max_pages - 1

        self.add_item(PaginationButton(label='⬅', style=discord.ButtonStyle.primary, custom_id='prev_page', disabled=prev_disabled))
        self.add_item(discord.ui.Button(label=f'{self.current_page + 1}/{self.max_pages}', style=discord.ButtonStyle.secondary, disabled=True))
        self.add_item(PaginationButton(label='➡', style=discord.ButtonStyle.primary, custom_id='next_page', disabled=next_disabled))

    def update_buttons(self):
        self.clear_items()
        self.set_options(self.options)
        self.add_buttons()

    def get_response(self):
        response = list(self.response.values()) if not self.styled_values else list(self.response.keys())
        if len(response) > 1:
            return response
        elif len(response) == 1:
            return response[0]
        else:
            return []

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        if self.required and not self.response:
            embed = response_error_embed("command-required-interaction", self.locale)
            return await interaction.message.channel.send(
                embed=embed,
                delete_after=10,
                mention_author=True,
            )
        await self.callback(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"OptionsView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )

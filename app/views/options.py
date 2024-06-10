from typing import Callable, Dict, List, Union

import discord

from app.components.buttons import CancelButton, ConfirmButton, OptionsButton
from app.components.embed import response_error_embed


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
        self.callback = callback
        self.locale = locale
        self.required = required
        self.unique = unique
        self.response = dict()
        self.styled_values = styled_values
        super().__init__()
        self.set_options(options)
        self.add_item(ConfirmButton(callback=self._confirm_callback, locale=locale))
        self.add_item(CancelButton(locale=locale))

    def set_options(self, options: list[str, str]) -> None:
        if not self.styled_values:
            for index, option in enumerate(options):
                option_button = OptionsButton(
                    options_custom_id=str(index), options_label=str(option), unique=self.unique
                )
                self.add_item(option_button)
        else:
            for key, value in options.items():
                option_button = OptionsButton(
                    options_custom_id=str(value), options_label=str(key), unique=self.unique
                )
                self.add_item(option_button)

    def get_response(self):
        if self.styled_values:
            return list(self.response.keys())
        return list(self.response.values())

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        if self.required and not self.response:
            embed = response_error_embed("command-required-interaction", self.locale)
            return await interaction.message.channel.send(
                embed=embed,
                delete_after=10,
                mention_author=True,
            )
        await self.callback(interaction)

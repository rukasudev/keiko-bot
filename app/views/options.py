from typing import Callable, List

import discord

from app.components.buttons import CancelButton, ConfirmButton, OptionsButton
from app.components.embed import response_error_embed


class OptionsView(discord.ui.View):
    def __init__(
        self,
        options: List[str],
        callback: Callable,
        locale: str,
        required: bool = False,
    ) -> None:
        self.callback = callback
        self.locale = locale
        self.required = required
        self.response = dict()
        super().__init__()
        self.set_options(options)
        self.add_item(ConfirmButton(callback=self._confirm_callback, locale=locale))
        self.add_item(CancelButton(locale=locale))

    def set_options(self, options: list[str, str]) -> None:
        for index, option in enumerate(options):
            option_button = OptionsButton(
                options_custom_id=str(index), options_label=str(option)
            )
            self.add_item(option_button)

    def get_response(self):
        return self.response.values()

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        if self.required and not self.response:
            embed = response_error_embed("command-required-interaction", self.locale)
            return await interaction.message.channel.send(
                embed=embed,
                delete_after=10,
                mention_author=True,
            )
        await self.callback(interaction)

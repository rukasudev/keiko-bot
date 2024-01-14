from typing import Callable, List
from app.components.buttons import CancelButton, ConfirmButton, OptionsButton

import discord


class OptionsView(discord.ui.View):
    def __init__(
        self,
        options: List[str],
        callback: Callable = None,
    ) -> None:
        self.options = options
        self.callback = callback
        self.selected = dict()
        super().__init__()
        self.set_options(self.options)
        self.add_item(ConfirmButton(callback=self._confirm_callback))
        self.add_item(CancelButton())

    def set_options(self, options: list[str, str]) -> None:
        for index, option in enumerate(options):
            option_button = OptionsButton(
                options_custom_id=str(index), options_label=str(option)
            )
            self.add_item(option_button)

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        await self.callback(interaction)

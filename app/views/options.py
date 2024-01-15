from typing import Callable, List
from i18n import t
from app.components.buttons import CancelButton, ConfirmButton, OptionsButton

import discord


class OptionsView(discord.ui.View):
    def __init__(
        self,
        options: List[str],
        callback: Callable,
        locale: str,
        required: bool = False
    ) -> None:
        self.options = options
        self.callback = callback
        self.locale = locale
        self.required = required
        self.response = dict()
        super().__init__()
        self.set_options(self.options)
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
            return await interaction.message.channel.send(
                t("errors.command-required-interaction.message", locale=self.locale),
                delete_after=10,
                mention_author=True
            )
        await self.callback(interaction)

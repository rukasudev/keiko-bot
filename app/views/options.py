from typing import Callable, List

import discord

from app import redis_client
from app.components.buttons import CancelButtom, ConfirmButton, OptionsButton


class OptionsView(discord.ui.View):
    def __init__(
        self,
        command_key: str,
        redis_key: str,
        options: List[str],
        callback: Callable = None,
        cache: bool = False,
    ):
        self.options = options
        self.command_key = command_key
        self.redis_key = redis_key
        self.callback = callback
        self.cache = cache
        self.selected = dict()
        super().__init__()
        self.set_options(self.options)
        self.add_item(ConfirmButton(callback=self._confirm_callback))
        self.add_item(CancelButtom())

    def set_options(self, options: list[str, str]):
        for index, option in enumerate(options):
            option_button = OptionsButton(
                options_custom_id=str(index), options_label=str(option)
            )
            self.add_item(option_button)

    async def _confirm_callback(self, interaction: discord.Interaction):
        if self.cache:
            redis_key = f"{interaction.guild.id}@{self.command_key}:{self.redis_key}"
            redis_client.lpush(redis_key, *self.selected.values())

        await self.callback(interaction)

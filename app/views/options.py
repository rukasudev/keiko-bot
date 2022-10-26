import discord

from app import redis_client
from app.components.buttons import OptionsButton, ConfirmButton
from typing import Callable


class OptionsView(discord.ui.View):
    def __init__(
        self,
        command_key: str,
        redis_key: str,
        options: list[str, str],
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

    def set_options(self, options: list[str, str]):
        for index, option in enumerate(options):
            option_button = OptionsButton(
                options_custom_id=str(index),
                options_label=str(option),
                callback=self._callback,
            )
            self.add_item(option_button)

        self.add_item(ConfirmButton(callback=self._confirm_callback))

    async def _callback(self, interaction: discord.Interaction):
        index = int(interaction.data["custom_id"])

        if self.children[index].style == discord.ButtonStyle.primary:
            self.children[index].style = discord.ButtonStyle.gray
            del self.selected[index]
        else:
            self.children[index].style = discord.ButtonStyle.primary
            self.selected[index] = self.children[index].label

        await interaction.response.edit_message(view=self)

    async def _confirm_callback(self, interaction: discord.Interaction):
        if self.cache:
            redis_key = f"{interaction.guild.id}@{self.command_key}:{self.redis_key}"
            redis_client.lpush(redis_key, *self.selected.values())

        await self.callback(interaction)

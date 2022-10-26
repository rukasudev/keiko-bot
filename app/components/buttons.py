import discord

from typing import Callable


class ConfirmButton(discord.ui.Button):
    def __init__(self, callback: Callable) -> None:
        self.callback = callback
        super().__init__(label="✔️", style=discord.ButtonStyle.green)


class OptionsButton(discord.ui.Button):
    def __init__(
        self, options_label: str, options_custom_id: str, callback: Callable
    ) -> None:
        self.callback = callback
        super().__init__(
            label=options_label,
            style=discord.ButtonStyle.gray,
            custom_id=options_custom_id,
        )

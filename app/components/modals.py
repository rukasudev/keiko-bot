from typing import Callable

import discord


class CustomModal(discord.ui.Modal):
    def __init__(self, config: dict, callback: Callable) -> None:
        self.callback = callback
        super().__init__(title=config.get("title"), timeout=300)
        self.add_item(
            discord.ui.TextInput(
                style=discord.TextStyle.short,
                label=config.get("description", ""),
                placeholder=config.get("placeholder", ""),
                default=config.get("default", ""),
                required=config.get("required", True),
                max_length=config.get("max_length", 40),
            )
        )

    async def on_submit(self, interaction) -> None:
        await self.callback(interaction)

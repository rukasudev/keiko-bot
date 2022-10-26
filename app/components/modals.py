import discord

from app import redis_client
from typing import Callable


class CustomModal(discord.ui.Modal):
    def __init__(
        self,
        title: str,
        label: str,
        max_length: int,
        callback: Callable,
        required: bool,
        command_key: str,
        redis_key: str,
        text_style: discord.TextStyle = discord.TextStyle.short,
        placeholder: str = "",
        default: str = "",
        cache: bool = False,
    ) -> None:
        self.callback = callback
        self.cache = cache
        self.command_key = command_key
        self.redis_key = redis_key
        super().__init__(title=title, timeout=300)
        self.add_item(
            discord.ui.TextInput(
                label=label,
                style=text_style,
                placeholder=placeholder,
                default=default,
                required=required,
                max_length=max_length,
            )
        )

    # TODO: transfer this cache logic to utils
    async def on_submit(self, interaction):
        if self.cache:
            redis_key = f"{interaction.guild.id}@{self.command_key}:{self.redis_key}"
            redis_client.set(redis_key, self.children[0].value)

        await self.callback(interaction)

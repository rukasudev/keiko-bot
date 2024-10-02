from typing import Callable

import discord


class CustomModal(discord.ui.Modal):
    def __init__(self, config: dict, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.lowercase = config.get("lowercase", False)
        super().__init__(title=config.get("title").get(locale, "en-us"), timeout=300)
        self.add_item(
            discord.ui.TextInput(
                style=discord.TextStyle.long if config.get("multiline", False) else discord.TextStyle.short,
                label=config.get("label", "").get(locale, "en-us"),
                placeholder=config.get("placeholder", "").get(locale, "en-us"),
                default=config.get("default", "").get(locale, "en-us") if config.get("default") else None,
                required=config.get("required", True),
                max_length=config.get("max_length", 40),
            )
        )

    def get_response(self):
        if not hasattr(self, "response"):
            return None
        return self.response

    async def on_submit(self, interaction) -> None:
        self.response = self.children[0].value if not self.lowercase else self.children[0].value.lower()
        await self.callback(interaction)


class ConfirmationModal(discord.ui.Modal):
    def __init__(self, action: str, locale: str, callback: Callable) -> None:
        from app.services.utils import parse_confirmation_desc, parse_confirmation_title

        self.action = action
        self.callback = callback
        super().__init__(title=parse_confirmation_title(action, locale))
        self.add_item(
            discord.ui.TextInput(
                style=discord.TextStyle.short,
                label=parse_confirmation_desc(action, locale),
                required=True,
                max_length=len(action),
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if str(self.children[0].value).lower() == self.action.lower():
            return await self.callback(interaction)

        await interaction.response.defer()

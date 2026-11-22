from typing import Any, Dict, List, Optional, TypeVar

import discord

from app.constants import DiscordLimits as limits
from app.core.servers import interaction_language

V = TypeVar("V", bound=discord.ui.View, covariant=True)


def _fit(text: Optional[str]) -> Optional[str]:
    if text in (None, ""):
        return None
    text = str(text)
    if len(text) <= limits.SELECT_OPTION_TEXT:
        return text
    return f"{text[:limits.SELECT_OPTION_TEXT - 1]}…"


class Select(discord.ui.Select[V]):
    def __init__(self, placeholder: str, options: Dict[str, str], custom_callback=None, unique=False, custom_id=None):
        self.parsed_options = self.parse_options(options)
        self.custom_callback = custom_callback
        kwargs = {}
        if custom_id:
            kwargs["custom_id"] = custom_id
        super().__init__(
            placeholder=placeholder,
            options=self.parsed_options,
            max_values=unique or len(self.parsed_options),
            **kwargs,
        )

    def parse_options(self, options_dict: Dict[str, Any]) -> List[discord.SelectOption]:
        options = []

        if not options_dict:
            return options

        for key, item in options_dict.items():
            label, description = item, None
            if isinstance(item, dict):
                label, description = item.get("label"), item.get("description")
            option = discord.SelectOption(
                label=_fit(label), value=key, description=_fit(description)
            )
            options.append(option)

        return options

    async def callback(self, interaction: discord.Interaction):
        await interaction_language(interaction)
        self.view.selected_options = self.values
        if self.custom_callback:
            await self.custom_callback(interaction)
        else:
            await self.view.callback(interaction)

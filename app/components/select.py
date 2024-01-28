from typing import Dict, List
from i18n import t

import discord

class Select(discord.ui.Select):
    def __init__(self, placeholder: str, options: Dict[str, str]):
        self.parsed_options = self.parse_options(options)
        super().__init__(
            placeholder=placeholder,
            options=self.parsed_options, max_values=len(self.parsed_options)
        )

    def parse_options(self, options_dict: Dict[str, str]) -> List[discord.SelectOption]:
        options = []
        for key, label in options_dict.items():
            option = discord.SelectOption(label=label, value=key)
            options.append(option)

        return options

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_options = self.values
        await self.view.callback(interaction)

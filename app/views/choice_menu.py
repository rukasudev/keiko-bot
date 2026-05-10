from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

import discord


@dataclass
class ChoiceConfig:
    label: str
    callback: Callable[[discord.Interaction, "ChoiceMenuView"], Awaitable[None]]
    emoji: Optional[str] = None
    style: discord.ButtonStyle = discord.ButtonStyle.grey


class ChoiceMenuView(discord.ui.View):
    def __init__(self, choices: List[ChoiceConfig], locale: str) -> None:
        super().__init__(timeout=1800)
        self.locale = locale
        for choice in choices:
            self.add_item(_ChoiceButton(choice, self))


class _ChoiceButton(discord.ui.Button):
    def __init__(self, choice: ChoiceConfig, parent_view: ChoiceMenuView) -> None:
        super().__init__(label=choice.label, emoji=choice.emoji, style=choice.style)
        self._choice = choice
        self._parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._choice.callback(interaction, self._parent_view)

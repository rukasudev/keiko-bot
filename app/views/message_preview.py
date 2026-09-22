from typing import Any, Dict, List, Sequence, Union

import discord

from app.constants import ViewConstants as view_constants
from app.services.utils import ml

Page = Union[str, discord.Embed]


class MessagePreviewView(discord.ui.View):
    """Several written messages previewed one at a time, with a Next button."""

    def __init__(self, pages: Sequence[Page], locale):
        super().__init__(timeout=view_constants.LONG_TIMEOUT_SECONDS)
        self.pages: List[Page] = [page for page in pages if page] or [
            ml("buttons.preview.empty", locale=locale)
        ]
        self.index = 0
        self.next_message.label = ml("buttons.preview.next", locale=locale)

    @property
    def page(self) -> Page:
        return self.pages[self.index]

    @property
    def content(self) -> str:
        page = self.page
        return page if isinstance(page, str) else ""

    def _payload(self) -> Dict[str, Any]:
        page = self.page
        return {"embed": page} if isinstance(page, discord.Embed) else {"content": page}

    async def send(self, interaction: discord.Interaction):
        await interaction.followup.send(
            **self._payload(),
            view=self if len(self.pages) > 1 else discord.utils.MISSING,
            ephemeral=True,
        )

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next_message(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.edit_message(**self._payload(), view=self)

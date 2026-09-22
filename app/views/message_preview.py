from typing import Any, Dict, List, Optional, Sequence, Union

import discord

from app.constants import ViewConstants as view_constants
from app.services.utils import ml

Page = Union[str, discord.Embed]


class MessagePreviewView(discord.ui.View):
    """Several written messages previewed one at a time, with a Next button.

    A page is a written message or an embed already drawn. An `embed` given
    here rides along with every page, so a preview shows the whole
    announcement and not only the text above it.
    """

    def __init__(
        self,
        pages: Sequence[Page],
        locale,
        embed: Optional[discord.Embed] = None,
    ):
        super().__init__(timeout=view_constants.LONG_TIMEOUT_SECONDS)
        self.pages: List[Page] = [page for page in pages if page] or [
            ml("buttons.preview.empty", locale=locale)
        ]
        self.index = 0
        self.embed = embed
        self.next_message.label = ml("buttons.preview.next", locale=locale)

    @property
    def page(self) -> Page:
        return self.pages[self.index]

    @property
    def content(self) -> str:
        page = self.page
        return page if isinstance(page, str) else ""

    def _payload(self, sending: bool = False) -> Dict[str, Any]:
        page = self.page
        if isinstance(page, discord.Embed):
            return {"embed": page}
        blank = discord.utils.MISSING if sending else None
        return {"content": page, "embed": self.embed or blank}

    async def send(self, interaction: discord.Interaction):
        await interaction.followup.send(
            **self._payload(sending=True),
            view=self if len(self.pages) > 1 else discord.utils.MISSING,
            ephemeral=True,
        )

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next_message(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.edit_message(**self._payload(), view=self)

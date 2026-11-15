"""Keiko's generic buttons, and the copy block a button reads."""

from typing import Any, Callable

import discord
from pydantic import Field

from app.core.i18n import CopyBlock, CopyText
from app.core.servers import interaction_language
from app.core.utils import ml


class ButtonCopy(CopyBlock):
    """One button's copy: its label, and the caption the panel's Help screen lists."""

    label: CopyText = Field(
        description="The text on the button.",
        examples=[{"en-us": "Sync", "pt-br": "Sincronizar"}],
        json_schema_extra={"role": "copy"},
    )
    description: CopyText | None = Field(
        None,
        description="The caption the panel's Help screen lists beside the button, in "
        "the third person; a button a form panel draws writes it.",
        json_schema_extra={"role": "copy"},
    )


class BackButton(discord.ui.Button):
    def __init__(
        self, view: discord.ui.View, embed: discord.Embed, locale: str | discord.Locale
    ) -> None:
        self.old_view = view
        self.old_embed = embed
        super().__init__(
            label=ml("buttons.back.label", locale=locale),
            style=discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.edit_message(
            embed=self.old_embed, view=self.old_view
        )


class HistoryButton(discord.ui.Button):
    def __init__(self, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.desc = ml("buttons.history.description", locale=locale)
        super().__init__(
            label=ml("buttons.history.label", locale=locale),
            emoji="📜",
            style=discord.ButtonStyle.grey,
        )

class GenericButton(discord.ui.Button):
    def __init__(self, label: str, callback: Callable, style: discord.ButtonStyle, **kwargs):
        self.custom_callback = callback
        super().__init__(label=label, style=style, **kwargs)

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction_language(interaction)
        await self.custom_callback(interaction)

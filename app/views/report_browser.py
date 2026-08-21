"""One command, many read-only screens: a select swaps which one is showing.

Built for `/admin insights`, but nothing here knows about analytics: any
command that would otherwise become a handful of sibling commands can compose
it instead. Reference: docs/analytics.md
"""
from typing import Any, Callable, Dict, List, Optional

import discord

from app.components.select import Select
from app.constants import ViewConstants as view_constants


class ReportSection:
    """One screen: a label for the menu and a callable that builds its embed."""

    def __init__(
        self,
        key: str,
        label: str,
        build: Callable[[], discord.Embed],
        emoji: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        self.key = key
        self.label = label
        self.build = build
        self.emoji = emoji
        self.description = description

    @property
    def menu_label(self) -> str:
        return f"{self.emoji} {self.label}" if self.emoji else self.label


class ReportBrowserView(discord.ui.View):
    """Holds the menu and edits its own message when the choice changes."""

    def __init__(self, sections: List[ReportSection], placeholder: str) -> None:
        super().__init__(timeout=view_constants.LONG_TIMEOUT_SECONDS)
        self.sections = {section.key: section for section in sections}
        self.add_item(Select(
            placeholder=placeholder,
            options={
                section.key: {
                    "label": section.menu_label,
                    "description": section.description,
                }
                for section in sections
            },
            custom_callback=self.show_selected,
            unique=1,
        ))

    async def show_selected(self, interaction: discord.Interaction) -> None:
        selected = self.selected_options[0] if self.selected_options else None
        section = self.sections.get(selected)
        if not section:
            return await interaction.response.defer()

        await interaction.response.edit_message(embed=section.build(), view=self)


class ReportBrowser:
    """Sends the first section and lets the menu move between the others."""

    def __init__(
        self,
        sections: List[Dict[str, Any]],
        placeholder: str,
        ephemeral: bool = True,
    ) -> None:
        self.sections = [ReportSection(**section) for section in sections]
        self.placeholder = placeholder
        self.ephemeral = ephemeral

    async def send(self, interaction: discord.Interaction) -> None:
        if not self.sections:
            return

        view = ReportBrowserView(self.sections, self.placeholder)
        embed = self.sections[0].build()

        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed, view=view, ephemeral=self.ephemeral
            )
            return

        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=self.ephemeral
        )

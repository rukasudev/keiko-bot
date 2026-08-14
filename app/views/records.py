"""Generic records browser: fetch records, format them into pagination
fields, and optionally filter by member through a select round trip."""
from typing import Any, Callable, Dict, List, Optional

import discord

from app.components.buttons import GenericButton
from app.components.embed import base_embed
from app.components.select_views import UserSelectView
from app.services.utils import ml, parse_locale
from app.views.pagination import PaginationView


class RecordsBrowser:
    """Browse screen for a command's records.

    `fetch(interaction, user_id)` returns the raw records and `to_fields`
    turns them into the `{name: value}` dict `PaginationView` renders.
    `filter_namespace` enables the member filter and resolves its copy from
    the `{label, all-label, title, description, empty}` leaf keys.
    """

    def __init__(
        self,
        fetch: Callable[[discord.Interaction, Optional[str]], List[Any]],
        to_fields: Callable[[List[Any], discord.Interaction], Dict[str, str]],
        title: str,
        description: str = "",
        sep: int = 4,
        empty_description: Optional[str] = None,
        filter_namespace: Optional[str] = None,
        ephemeral: bool = True,
    ) -> None:
        self.fetch = fetch
        self.to_fields = to_fields
        self.title = title
        self.description = description
        self.sep = sep
        self.empty_description = empty_description
        self.filter_namespace = filter_namespace
        self.ephemeral = ephemeral

    async def send(
        self, interaction: discord.Interaction, user_id: Optional[str] = None
    ) -> None:
        records = self.fetch(interaction, user_id)

        if not records:
            empty = self._empty_copy(interaction, filtered=bool(user_id))
            if empty:
                return await interaction.response.send_message(
                    embed=base_embed(self.title, empty), ephemeral=self.ephemeral
                )

        view = PaginationView(
            interaction,
            title=self.title,
            description=self.description,
            data=self.to_fields(records, interaction),
            sep=self.sep,
        )
        if self.filter_namespace:
            view.add_item(self._filter_button(interaction, filtered=bool(user_id)))
        await view.send(ephemeral=self.ephemeral)

    def _empty_copy(self, interaction: discord.Interaction, filtered: bool) -> str:
        if filtered and self.filter_namespace:
            locale = parse_locale(interaction.locale)
            return ml(f"{self.filter_namespace}.empty", locale=locale)
        return self.empty_description or ""

    def _filter_button(
        self, interaction: discord.Interaction, filtered: bool
    ) -> discord.ui.Button:
        """Row 1 keeps the filter off the pagination-arrows row."""
        locale = parse_locale(interaction.locale)
        label_key = "all-label" if filtered else "label"
        return GenericButton(
            label=ml(f"{self.filter_namespace}.{label_key}", locale=locale),
            callback=self.send if filtered else self._open_member_picker,
            style=discord.ButtonStyle.grey,
            emoji="🔎",
            row=1,
        )

    async def _open_member_picker(self, interaction: discord.Interaction) -> None:
        locale = parse_locale(interaction.locale)

        async def on_selected(select_interaction: discord.Interaction) -> None:
            selected = picker.get_response()
            if isinstance(selected, (list, tuple)):
                selected = selected[0] if selected else None
            if not selected:
                return
            await self.send(select_interaction, user_id=str(selected))

        picker = UserSelectView(
            callback=on_selected, locale=locale, required=True, unique=True
        )
        embed = base_embed(
            ml(f"{self.filter_namespace}.title", locale=locale),
            ml(f"{self.filter_namespace}.description", locale=locale),
        )
        await interaction.response.edit_message(embed=embed, view=picker)

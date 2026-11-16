"""Generic records browser: fetch records, format them into pagination
fields, and optionally filter by member through a select round trip."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

import discord
from pydantic import Field

from app.constants import KeikoIcons as icons
from app.constants import ViewConstants as view_constants
from app.core import i18n
from app.core.commands import Open
from app.core.facts import Facts
from app.core.i18n import CopyBlock, CopyText
from app.core.utils import fill, ml, parse_locale
from app.ui.buttons import GenericButton
from app.ui.embed import base_embed
from app.ui.pagination import PaginationView


class RecordsDetail(CopyBlock):
    """The screen a record's select opens, with a way back to the list."""

    title: CopyText = Field(
        description="The detail's title; `$name` marks a value the code fills.",
        json_schema_extra={"role": "copy"},
    )
    description: CopyText = Field(
        description="The detail's body; `$name` marks a value the code fills.",
        json_schema_extra={"role": "copy"},
    )


class RecordsFilter(CopyBlock):
    """The member filter of a records list: its button, its picker and its empty
    state."""

    label: CopyText = Field(
        description="The filter button while the whole list shows.",
        examples=[{"en-us": "Filter by member", "pt-br": "Filtrar por membro"}],
        json_schema_extra={"role": "copy"},
    )
    clear_label: CopyText = Field(
        description="The same button once the list shows one member, which brings "
        "every record back.",
        json_schema_extra={"role": "copy"},
    )
    title: CopyText = Field(
        description="The member picker's title.",
        json_schema_extra={"role": "copy"},
    )
    description: CopyText = Field(
        description="The member picker's intro.",
        json_schema_extra={"role": "copy"},
    )
    empty: CopyText = Field(
        description="Drawn alone when the member picked has no record.",
        json_schema_extra={"role": "copy"},
    )


class Records(CopyBlock):
    """One browsable list, drawn by `RecordsBrowser(copy=...)` or a command's own
    `PaginationView`."""

    title: CopyText = Field(
        description="The list's title, with one leading emoji; `$name` marks a value "
        "the code fills.",
        examples=[{"en-us": "📜 History", "pt-br": "📜 Histórico"}],
        json_schema_extra={"role": "copy"},
    )
    description: CopyText | None = Field(
        None,
        description="The line above the records; `$name` marks a value the code fills.",
        json_schema_extra={"role": "copy"},
    )
    empty: CopyText | None = Field(
        None,
        description="Drawn alone when nothing is found; absent, an empty page is "
        "drawn.",
        json_schema_extra={"role": "copy"},
    )
    placeholder: CopyText | None = Field(
        None,
        description="The placeholder of the select that follows the page and opens a "
        "record (/help).",
        json_schema_extra={"role": "copy"},
    )
    detail: RecordsDetail | None = Field(
        None,
        description="The screen that select opens (/help).",
        json_schema_extra={"role": "structure"},
    )
    filter: RecordsFilter | None = Field(
        None,
        description="The member filter; absent, the list has no filter button.",
        json_schema_extra={"role": "structure"},
    )
    lines: Open[CopyText] = Field(
        default_factory=dict,
        description="Line templates the formatter fills once per record ($user, "
        "$channel, $date), keyed by the part of the record each draws.",
        json_schema_extra={"role": "copy"},
    )


@dataclass(frozen=True)
class _Filter:
    label: str
    clear_label: str
    title: str
    description: str
    empty: str


@dataclass(frozen=True)
class _Shown:
    title: str
    description: str
    empty: Optional[str]
    filter: Optional[_Filter]


class RecordsBrowser:
    """Browse screen for a command's records.

    `fetch(interaction, user_id)` is awaited for the raw records and `to_fields`
    turns them into the `{name: value}` dict `PaginationView` renders. `copy` names
    the Records block every text comes from, its `$name`s filled from `facts`, in
    `locale` when given, else in the language of each interaction.
    """

    def __init__(
        self,
        fetch: Callable[[discord.Interaction, Optional[str]], Awaitable[List[Any]]],
        to_fields: Callable[[List[Any], discord.Interaction], Dict[str, str]],
        sep: int = 4,
        ephemeral: bool = True,
        *,
        copy: str,
        facts: Optional[Facts] = None,
        locale: str = "",
    ) -> None:
        self.fetch = fetch
        self.to_fields = to_fields
        self.sep = sep
        self.ephemeral = ephemeral
        self.copy = copy
        self.facts = dict(facts or {})
        self.locale = locale

    async def send(
        self, interaction: discord.Interaction, user_id: Optional[str] = None
    ) -> None:
        """The list, or its empty state, as the answer to `interaction`."""
        shown = self._shown(interaction.locale)
        records = await self.fetch(interaction, user_id)

        if not records:
            empty = self._empty_copy(shown, filtered=bool(user_id))
            if empty:
                await interaction.response.send_message(
                    embed=base_embed(shown.title, empty), ephemeral=self.ephemeral
                )
                return

        view = PaginationView(
            interaction,
            title=shown.title,
            description=shown.description,
            data=self.to_fields(records, interaction),
            sep=self.sep,
        )
        if shown.filter:
            view.add_item(self._filter_button(shown.filter, filtered=bool(user_id)))
        await view.send(ephemeral=self.ephemeral)

    async def reply(
        self,
        message: discord.Message,
        data: Mapping[str, str],
        locale: str,
        thumbnail: Optional[str] = icons.IMAGE_02,
    ) -> None:
        """The list of `data`, already as fields, as the reply to a chat message: what
        a prefix command answers in place of `send`."""
        shown = self._shown(locale)
        view = PaginationView(
            None,
            shown.title,
            shown.description,
            data,
            sep=self.sep,
            thumbnail=thumbnail,
            locale=parse_locale(locale),
        )
        await view.reply(message)

    def empty_text(self, locale: str) -> str:
        """The list's empty state in `locale`, for another screen that says the same."""
        return self._shown(locale).empty or ""

    def _shown(self, locale: Any) -> _Shown:
        language = parse_locale(self.locale or locale)
        records, values = i18n.resolved(self.copy, Records, self.facts)

        def said(text: Any) -> str:
            return fill(text.get(language), **values) if text else ""

        written = records.filter
        found = (
            _Filter(
                label=said(written.label),
                clear_label=said(written.clear_label),
                title=said(written.title),
                description=said(written.description),
                empty=said(written.empty),
            )
            if written
            else None
        )
        empty = said(records.empty) if records.empty else None
        return _Shown(said(records.title), said(records.description), empty, found)

    def _empty_copy(self, shown: _Shown, filtered: bool) -> str:
        if filtered and shown.filter:
            return shown.filter.empty
        return shown.empty or ""

    def _filter_button(self, found: _Filter, filtered: bool) -> discord.ui.Button[Any]:
        """Row 1 keeps the filter off the pagination-arrows row."""
        return GenericButton(
            label=found.clear_label if filtered else found.label,
            callback=self.send if filtered else self._open_member_picker,
            style=discord.ButtonStyle.grey,
            emoji="🔎",
            row=1,
        )

    async def _open_member_picker(self, interaction: discord.Interaction) -> None:
        locale = parse_locale(self.locale or interaction.locale)
        found = self._shown(locale).filter
        assert found is not None

        async def on_selected(select_interaction: discord.Interaction) -> None:
            selected = picker.get_response()
            if isinstance(selected, (list, tuple)):
                selected = selected[0] if selected else None
            user_id = str(selected) if selected else None

            await self.send(select_interaction, user_id=user_id)

        picker = MemberPicker(on_selected, locale)
        embed = base_embed(found.title, found.description)
        await interaction.response.edit_message(embed=embed, view=picker)


class MemberPicker(discord.ui.View):
    """One member select and a confirm button, for the records filter."""

    def __init__(self, callback: Callable[[discord.Interaction], Any], locale: str):
        super().__init__(timeout=view_constants.LONG_TIMEOUT_SECONDS)
        self.chosen: Callable[[discord.Interaction], Any] = callback
        self.response: Dict[str, str] = {}
        self.user_select: discord.ui.UserSelect[Any] = discord.ui.UserSelect(
            placeholder=ml("texts.placeholders.user", locale=locale),
            min_values=0,
            max_values=1,
        )
        self.user_select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(self.user_select)
        self.add_item(
            GenericButton(
                ml("buttons.confirm.label", locale=locale),
                self._on_confirm,
                discord.ButtonStyle.green,
            )
        )
        self.add_item(
            GenericButton(
                ml("buttons.cancel.label", locale=locale),
                self._on_cancel,
                discord.ButtonStyle.grey,
            )
        )

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.response = {
            str(user.id): user.display_name for user in self.user_select.values
        }
        await interaction.response.defer()

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        await self.chosen(interaction)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.response = {}
        await self.chosen(interaction)

    def get_response(self) -> Any:
        """The member picked, or every member picked when there is more than one."""
        keys = list(self.response.keys())
        return keys[0] if len(keys) == 1 else keys

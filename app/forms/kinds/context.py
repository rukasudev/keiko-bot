"""What a kind may look at while rendering or parsing, besides the session."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.forms.definitions.schema import FormDefinition, Step
from app.forms.engine.rules import Scope
from app.forms.engine.screen import Button


@dataclass(frozen=True)
class PanelRow:
    """One line of the manager panel, already localized."""

    key: str
    title: str
    value: Any
    style: str | None = None
    icon: str | None = None
    group: str | None = None
    group_title: str | None = None
    group_icon: str | None = None
    hidden: bool = False


@dataclass(frozen=True)
class RenderContext:
    """The definition, the steps in play, the scope chain and prefetched data."""

    definition: FormDefinition
    steps: Sequence[Step]
    locale: str
    scope: Scope
    parent_values: Mapping[str, Any] = field(default_factory=dict)
    document: Mapping[str, Any] = field(default_factory=dict)
    items: Sequence[Mapping[str, Any]] = ()
    external: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    server_name: str = ""
    previews: Mapping[str, str] = field(default_factory=dict)
    panel_rows: Sequence[PanelRow] | None = None
    panel_info: str = ""
    panel_info_title: str = ""
    extra_buttons: Sequence[Button] = ()
    enabled: bool = True
    can_go_back: bool = False
    item_index: int | None = None

"""What a step looks like, with no Discord in it.

A screen carries resolved copy and abstract components; the renderer turns it
into an embed with a view, a Components V2 container or a modal. Buttons are
declared in the order they render, with Cancel last by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

Flavour = Literal["embed", "components_v2", "modal"]
Style = Literal["primary", "secondary", "success", "danger"]


@dataclass(frozen=True)
class Button:
    """A clickable button and the action it reports."""

    label: str
    action: str
    style: Style = "secondary"
    emoji: str | None = None
    disabled: bool = False
    description: str = ""


@dataclass(frozen=True)
class ChoiceOption:
    """One option of a choice, with its current selection."""

    label: str
    value: str
    style: Style = "secondary"
    selected: bool = False
    description: str = ""


@dataclass(frozen=True)
class Choice:
    """A grid of option buttons."""

    step_key: str
    options: tuple[ChoiceOption, ...]
    auto_confirm: bool = False


@dataclass(frozen=True)
class Picker:
    """A native select of channels, roles or members."""

    step_key: str
    kind: Literal["channel", "role", "user"]
    placeholder: str
    selected: tuple[str, ...] = ()
    unique: bool = True
    slot: str | None = None
    available_only: bool = False
    action: str = "draft"
    required: bool = False


@dataclass(frozen=True)
class OptionSelect:
    """A select over declared options."""

    step_key: str
    placeholder: str
    options: tuple[ChoiceOption, ...]
    min_values: int = 1
    max_values: int = 1
    slot: str | None = None
    action: str = "draft"


@dataclass(frozen=True)
class Input:
    """One text input of a modal."""

    label: str
    key: str | None = None
    placeholder: str | None = None
    default: str | None = None
    required: bool = True
    max_length: int = 40
    multiline: bool = False


@dataclass(frozen=True)
class TextInputs:
    """The inputs of a modal."""

    step_key: str
    inputs: tuple[Input, ...]
    slot: str | None = None


@dataclass(frozen=True)
class FileInput:
    """A file input of a modal."""

    step_key: str
    label: str
    slot: str | None = None


@dataclass(frozen=True)
class DesignCard:
    """One entry of a design gallery."""

    key: str
    label: str
    description: str
    preview_url: str | None


@dataclass(frozen=True)
class Gallery:
    """A design gallery: header, one card per design, footer."""

    step_key: str
    header: str
    designs: tuple[DesignCard, ...]
    footer: str
    select_label: str


@dataclass(frozen=True)
class CardHeader:
    """The head of a card: title, description, lines and thumbnail."""

    title: str
    description: str
    lines: tuple[str, ...] = ()
    thumbnail: str = ""


@dataclass(frozen=True)
class SectionView:
    """One rendered card section: heading, preview, its buttons."""

    index: int
    key: str
    heading: str
    preview: tuple[str, ...]
    buttons: tuple[Button, ...]
    media: str | None = None


@dataclass(frozen=True)
class Card:
    """A Components V2 card with customizable sections."""

    step_key: str
    header: CardHeader
    sections: tuple[SectionView, ...]


@dataclass(frozen=True)
class PanelGroup:
    """A group of panel lines and the step whose button edits them."""

    key: str | None
    heading: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Panel:
    """The manager panel: header, grouped settings, info, buttons."""

    title: str
    intro: str
    groups: tuple[PanelGroup, ...]
    info: str = ""
    info_title: str = ""
    thumbnail: str = ""
    edit_label: str = ""


Component = Union[
    Choice, Picker, OptionSelect, TextInputs, FileInput, Gallery, Card, Panel
]


@dataclass(frozen=True)
class Field:
    """One named paragraph of an embed."""

    name: str
    value: str


@dataclass(frozen=True)
class Screen:
    """A whole message: copy, components and buttons, in one flavour."""

    title: str = ""
    description: str = ""
    footer: str = ""
    fields: tuple[Field, ...] = ()
    components: tuple[Component, ...] = ()
    buttons: tuple[Button, ...] = ()
    flavour: Flavour = "embed"
    thumbnail: str | None = None
    image: str | None = None
    color: str | None = None
    modal_title: str = ""
    keep_previous: bool = False
    layout_footer: str = ""

    @property
    def is_layout(self) -> bool:
        """True for a Components V2 message."""
        return self.flavour == "components_v2"


def cancel_last(buttons: tuple[Button, ...]) -> tuple[Button, ...]:
    """The same buttons with every `cancel` at the end, in their order."""
    others = tuple(button for button in buttons if button.action != "cancel")
    cancels = tuple(button for button in buttons if button.action == "cancel")
    return others + cancels

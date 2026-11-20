"""What a form YAML may declare, how it is read and how it is compiled."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, TypeVar, Union, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from app.constants import DiscordLimits, ViewConstants
from app.core.commands import Open, command_for, commands, form_file, strict_yaml
from app.core.commands import Text as Text
from app.core.conditions import (
    Leaf,
    Override,
    Scalar,
    Scope,
    When,
    leaves,
    same_value,
    winner,
)
from app.core.facts import (
    TextError,
    context_entry,
    fact_hint,
    finished_texts,
    global_catalog,
    with_context,
    written_texts,
)
from app.core.tokens import Token, TokenError, hint, tokens
from app.forms.form.responses.styles import TOKEN_FORMATTERS, stored_style
from app.forms.form.responses.transforms import NORMALIZERS, TRANSFORMS
from app.forms.form.responses.validations import VALIDATORS


class DefinitionSource(Protocol):
    """A place raw form definitions are read from."""

    def load(self, key: str) -> Mapping[str, Any]:
        """The raw definition of the form `key`."""

    def list(self) -> tuple[str, ...]:
        """Every form key the source knows."""


class DiskSource:
    """The `form.yml` of each command, found through the registry."""

    def load(self, key: str) -> Mapping[str, Any]:
        """The parsed `form.yml` of the command `key`, read as strictly as its copy."""
        path = form_file(command_for(key.lower()).key)
        if path is None:
            raise FileNotFoundError(f"the command {key!r} writes no form")
        loaded = strict_yaml(path.read_text(encoding="utf-8"), str(path)) or {}
        if not isinstance(loaded, Mapping):
            raise ValueError(f"{path} is not a mapping")
        return loaded

    def list(self) -> tuple[str, ...]:
        """Every command key whose folder writes a `form.yml`."""
        return tuple(
            sorted(item.key for item in commands() if form_file(item.key) is not None)
        )


Locale = Literal["en-us", "pt-br"]
LOCALES: tuple[Locale, ...] = ("en-us", "pt-br")

ValueFormat = Literal[
    "channel",
    "role",
    "user",
    "bullet",
    "code_block",
    "numbered",
    "code",
    "boolean",
    "customized",
    "month_day",
]
TOKEN_FORMATS: tuple[str, ...] = (*get_args(ValueFormat), *TOKEN_FORMATTERS)
ButtonStyle = Literal["primary", "secondary", "success", "danger"]
Appearance = Literal["everywhere", "setup", "panel"]


def _open(child: str, description: str, *, role: str, required: bool = False) -> Any:
    extra: dict[str, JsonValue] = {"open": child, "role": role}
    if required:
        return Field(description=description, json_schema_extra=extra)
    return Field(default_factory=dict, description=description, json_schema_extra=extra)


def _filled(description: str, *, role: str) -> Any:
    def filled(schema: dict[str, JsonValue]) -> None:
        schema.pop("default", None)
        schema["role"] = role

    return Field(None, description=description, json_schema_extra=filled)


class Node(BaseModel):
    """Base of every definition model: frozen, strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Extension(Node):
    """A registered validator, transform or normalizer and the params it takes."""

    name: str = Field(
        description="The registered name the bot runs the value through: a validator "
        "under `validation`, a transform under `transform`, a normalizer under "
        "`normalize`.",
        examples=["link_or_domain", "date", "normalize_link", "lowercase"],
        json_schema_extra={"role": "behavior"},
    )
    params: Open[str] = _open(
        "the validator's params -> an answer key or a value",
        "What the extension reads besides the typed value; the compiler asks for "
        "exactly the params its declaration names, none for a transform or a "
        "normalizer.",
        role="data",
    )


Default = TypeVar("Default", Text, Text | None)


class TextOverride(Override):
    """Texts that replace the default ones while the rule holds; the first item whose
    rule holds wins and replaces only the texts it writes."""

    title: Text | None = Field(
        None,
        description="The title shown while the rule holds.",
        json_schema_extra={"role": "copy"},
    )
    description: Text | None = Field(
        None,
        description="The description shown while the rule holds.",
        json_schema_extra={"role": "copy"},
    )

    def text(self, part: Literal["title", "description"]) -> Text | None:
        """The item's title or description, None when it replaces only the other."""
        return self.title if part == "title" else self.description


def overridden_text(
    default: Default,
    overrides: Sequence[TextOverride],
    part: Literal["title", "description"],
    scope: Scope | None,
) -> Default | Text:
    """The `part` the first override whose rule holds writes, else `default`."""
    chosen = winner(overrides, scope)
    text = chosen.text(part) if chosen is not None else None
    return default if text is None else text


class ButtonSpec(Node):
    """A button the form labels."""

    label: Text = Field(
        description="The text on the button.",
        json_schema_extra={"role": "copy"},
    )


class Screen(Node):
    """The texts of a screen of its own: its title and the intro under it."""

    title: Text | None = Field(
        None,
        description="The screen's title; its owner's label when left out.",
        json_schema_extra={"role": "copy"},
    )
    description: Text | None = Field(
        None,
        description="The intro under the screen's title; none when left out.",
        json_schema_extra={"role": "copy"},
    )


class PanelGroupSpec(Node):
    """A heading several settings share on the panel and the review."""

    title: Text | None = Field(
        None,
        description="The heading over the group's rows.",
        json_schema_extra={"role": "copy"},
    )
    emoji: str | None = Field(
        None,
        description="The emoji before the heading.",
        json_schema_extra={"role": "visual"},
    )
    screen: Screen | None = Field(
        None,
        description="The intro of the group's own screen under `description`; the "
        "screen is titled by the group's `title`.",
        json_schema_extra={"role": "structure"},
    )
    overrides: tuple[TextOverride, ...] = Field(
        (),
        description="A heading and a screen intro that replace `title` and "
        "`screen.description` while a rule holds, for a group named after the mode "
        "in force; the first item whose rule holds wins.",
        json_schema_extra={"role": "conditions"},
    )
    opens: Literal["inline", "screen", "button"] = Field(
        "inline",
        description="Where the group's rows are edited: listed on the panel, on a "
        "screen of their own behind one Edit, or on that screen opened from a button "
        "on the panel's last row, for settings that stay out of a list's way.",
        json_schema_extra={"role": "visual"},
    )
    button: ButtonSpec | None = Field(
        None,
        description="The button that opens the group, with `opens: button`.",
        json_schema_extra={"role": "structure"},
    )
    order: int = Field(
        0,
        description="Where the heading sorts among the panel's headings, lowest first.",
        json_schema_extra={"role": "visual"},
    )

    @model_validator(mode="after")
    def _screen_and_button(self) -> PanelGroupSpec:
        if self.screen is not None and self.screen.title is not None:
            raise ValueError("a group's screen is titled by the group's title")
        if (self.opens == "button") != (self.button is not None):
            raise ValueError("a group opened from a button labels it, and only then")
        return self

    def opens_own_screen(self) -> bool:
        """True when the group's rows open on a screen of their own."""
        return self.opens != "inline"

    def screen_description(self) -> Text | None:
        """The intro of the group's own screen, None for none."""
        return self.screen.description if self.screen else None


class Option(Node):
    """One choice of a single-choice step or a card section."""

    label: Text = Field(
        description="The text of the choice's button or select row.",
        json_schema_extra={"role": "copy"},
    )
    value: Scalar = Field(
        description="The value stored when the choice is picked; rules compare "
        "with it.",
        json_schema_extra={"role": "data"},
    )
    style: ButtonStyle | None = Field(
        None,
        description="The colour of the choice's button, grey when left out.",
        json_schema_extra={"role": "visual"},
    )
    description: Text | None = Field(
        None,
        description="A line under the choice on its select and on its section's "
        "screen, for choices such as modes that need a sentence each.",
        json_schema_extra={"role": "copy"},
    )


class Design(Node):
    """One entry of a design gallery."""

    value: str = Field(
        description="The design id the bot stores and draws a preview of.",
        examples=["server_blur"],
        json_schema_extra={"role": "identity"},
    )
    label: Text = Field(
        description="The design's name on the gallery and summaries.",
        json_schema_extra={"role": "copy"},
    )
    description: Text = Field(
        description="What the design looks like, on the gallery.",
        json_schema_extra={"role": "copy"},
    )


class TextField(Node):
    """One input of a `modal_input` section's modal."""

    key: str | None = Field(
        None,
        description="The state key the input writes; inputs without one are joined "
        "with `;` into the section's `keys.value`.",
        json_schema_extra={"role": "data"},
    )
    label: Text = Field(
        description="The input's label on the modal.",
        json_schema_extra={"role": "copy"},
    )
    placeholder: Text | None = Field(
        None,
        description="The grey hint inside the empty input.",
        json_schema_extra={"role": "copy"},
    )
    max_length: int = Field(
        40,
        description="The most characters the input accepts.",
        json_schema_extra={"role": "behavior"},
    )
    required: bool = Field(
        True,
        description="Refuses the modal while it is empty.",
        json_schema_extra={"role": "behavior"},
    )
    multiline: bool = Field(
        False,
        description="Draws a paragraph input instead of a one-line one.",
        json_schema_extra={"role": "visual"},
    )
    normalize: Extension | None = Field(
        None,
        description="The registered normalizer the typed value goes through before it "
        "is checked and kept.",
        json_schema_extra={"role": "behavior"},
    )


class Select(Node):
    """One native select of a selects section."""

    type: Literal["channels", "roles", "assignable_roles"] = Field(
        description="What the select picks: channels, roles, or only the roles Keiko "
        "can assign.",
        json_schema_extra={"role": "identity"},
    )
    key: str = Field(
        description="The state key the picked ids are written to.",
        json_schema_extra={"role": "data"},
    )
    emoji: str | None = Field(
        None,
        description="Leads the select's line on the card, the section's emoji when "
        "left out.",
        json_schema_extra={"role": "visual"},
    )
    label: Text = Field(
        description="The name of the picked values on summaries.",
        json_schema_extra={"role": "copy"},
    )
    placeholder: Text = Field(
        description="The grey hint inside the empty select.",
        json_schema_extra={"role": "copy"},
    )

    def value_style(self) -> str:
        """How summaries format the picked ids: as channels or as roles."""
        return "channel" if self.type == "channels" else "role"


class ReviewLine(Node):
    """A line of the review read from an answer the document never keeps."""

    emoji: str = Field(
        "",
        description="Leads the line.",
        json_schema_extra={"role": "visual"},
    )
    label: Text = Field(
        description="The line's label.",
        json_schema_extra={"role": "copy"},
    )
    answer: str = Field(
        description="The answer the line shows; the line is left out while it "
        "is empty.",
        json_schema_extra={"role": "data"},
    )


Thumbnail = Literal["default", "nerd", "cake"]


class Header(Node):
    """The card header: its title and the picture of Keiko beside it."""

    title: Text | None = Field(
        None,
        description="The header title, after the card's emoji; the card's title when "
        "left out.",
        json_schema_extra={"role": "copy"},
    )
    thumbnail: Thumbnail | None = Field(
        None,
        description="The picture of Keiko drawn beside the header; none when left out.",
        json_schema_extra={"role": "visual"},
    )


class CardField(Node):
    """A persisted key of a card and how summaries show it."""

    key: str = Field(
        description="A key the card's Done saves; a state key no field names stays "
        "in the draft.",
        json_schema_extra={"role": "data"},
    )
    label: Text | None = Field(
        None,
        description="The key's name on summaries, the panel and the intro; the label "
        "of the section, select or input that owns the key when left out.",
        json_schema_extra={"role": "copy"},
    )
    emoji: str | None = Field(
        None,
        description="The emoji of the key's line when no section owns the key, for a "
        "value a transform writes.",
        json_schema_extra={"role": "visual"},
    )
    description: Text | None = Field(
        None,
        description="The line the intro lists for this setting.",
        json_schema_extra={"role": "copy"},
    )
    format: ValueFormat | None = Field(
        None,
        description="How summaries format the value; the format of the section or "
        "select that owns the key when left out.",
        json_schema_extra={"role": "visual"},
    )
    hidden: bool = Field(
        False,
        description="Keeps the key off the review, the panel and the intro, for "
        "bookkeeping keys such as a mode or an image url.",
        json_schema_extra={"role": "conditions"},
    )
    button: ButtonSpec | None = Field(
        None,
        description="The Edit beside the key's row, for a row whose Edit only adds; "
        "Edit when left out.",
        json_schema_extra={"role": "structure"},
    )
    group: str | None = Field(
        None,
        description="The `groups` entry whose heading lists the key's row with an "
        "Edit of its own, for a key that belongs with another step's rows.",
        json_schema_extra={"role": "structure"},
    )


class PreviewAnswer(Node):
    """A preview token filled with an answer."""

    answer: str = Field(
        description="The answer whose value replaces the token.",
        json_schema_extra={"role": "data"},
    )
    format: ValueFormat | None = Field(
        None,
        description="How the value is formatted before it replaces it.",
        json_schema_extra={"role": "visual"},
    )

    def value_style(self) -> str | None:
        """The style of the declared format of the answer."""
        return stored_style(self.format)


class PreviewContext(Node):
    """A preview token filled with a value of the interaction."""

    context: Literal["server_name"] = Field(
        description="The interaction value that replaces the token: the server's name.",
        json_schema_extra={"role": "data"},
    )


PreviewToken = Union[PreviewAnswer, PreviewContext]


class SectionKeys(Node):
    """The state keys a card section owns."""

    value: str | None = Field(
        None,
        description="The key the section's value is written to.",
        json_schema_extra={"role": "data"},
    )
    mode: str | None = Field(
        None,
        description="The key holding `default` or `custom` for a section that keeps "
        "a default until it is customized.",
        json_schema_extra={"role": "data"},
    )
    title: str | None = Field(
        None,
        description="The key a `title_content` section writes its title to.",
        json_schema_extra={"role": "data"},
    )
    description: str | None = Field(
        None,
        description="The key a `title_content` section writes its body to.",
        json_schema_extra={"role": "data"},
    )
    image: str | None = Field(
        None,
        description="The key a `file_upload` section writes the image url to.",
        json_schema_extra={"role": "data"},
    )

    def written(self) -> tuple[str, ...]:
        """Every key the section writes, in declaration order."""
        return tuple(
            key
            for key in (self.value, self.mode, self.title, self.description, self.image)
            if key
        )


class ResetRule(Node):
    """A dependent key a change clears when the validator rejects it with the change."""

    key: str = Field(
        description="The state key the change clears.",
        json_schema_extra={"role": "data"},
    )
    validation: Extension = Field(
        description="The validator that checks the dependent key against the "
        "new value.",
        json_schema_extra={"role": "behavior"},
    )


class ModalSpec(Node):
    """The modal a section opens."""

    title: Text = Field(
        description="The modal's title, after the section's emoji.",
        json_schema_extra={"role": "copy"},
    )


class TitleContentLabels(Node):
    """The labels of a `title_content` modal's two inputs."""

    title: Text | None = Field(
        None,
        description="The label of the title input.",
        json_schema_extra={"role": "copy"},
    )
    description: Text | None = Field(
        None,
        description="The label of the body input.",
        json_schema_extra={"role": "copy"},
    )


class TitleContentModal(ModalSpec):
    """The modal a `title_content` section opens: a title input and a body input."""

    labels: TitleContentLabels = Field(
        default_factory=TitleContentLabels,
        description="The labels of the title and the body inputs.",
        json_schema_extra={"role": "structure"},
    )
    normalize: Extension | None = Field(
        None,
        description="Runs a registered normalizer over the title and the body as "
        "they are saved.",
        json_schema_extra={"role": "behavior"},
    )


class InputsModal(ModalSpec):
    """The modal a `modal_input` section opens: its inputs and their check."""

    validation: Extension | None = Field(
        None,
        description="Runs a registered validator on submit, with any lookup it needs.",
        json_schema_extra={"role": "behavior"},
    )
    inputs: tuple[TextField, ...] = Field(
        (),
        description="The modal's inputs, five at most.",
        json_schema_extra={"role": "structure"},
    )


class Fills(Node):
    """Copy a typed value writes into another key of its card."""

    into: str = Field(
        description="The state key whose text the value is written into.",
        json_schema_extra={"role": "data"},
    )
    tokens: Open[str] = _open(
        "{token} names in that text -> a template with {value}",
        "Each `{token}` of the text becomes its template with the typed value, and a "
        "value typed earlier is rewritten.",
        required=True,
        role="behavior",
    )


class SectionBase(Node):
    """What every card section declares."""

    type: str = Field(
        description="The section type, which decides how it draws and what it edits."
    )
    key: str = Field(
        description="The section's id, unique in its card, named by groups and edits.",
        json_schema_extra={"role": "identity"},
    )
    emoji: str = Field(
        description="Leads the section's heading and lines.",
        json_schema_extra={"role": "visual"},
    )
    label: Text = Field(
        description="The section's heading on the card.",
        json_schema_extra={"role": "copy"},
    )
    description: Text | None = Field(
        None,
        description="Lines under the heading while the card is edited.",
        json_schema_extra={"role": "copy"},
    )
    keys: SectionKeys = Field(
        default_factory=SectionKeys,
        description="The state keys the section writes.",
        json_schema_extra={"role": "data"},
    )
    image: str = Field(
        "",
        description="Draws the picture held by that state key or lookup answer under "
        "the section's lines.",
        json_schema_extra={"role": "visual"},
    )
    when: When | None = Field(
        None,
        description="Shows the section only while the rule over the card's state "
        "holds; a hidden section is never required.",
        json_schema_extra={"role": "conditions"},
    )
    appears_in: Appearance = Field(
        "everywhere",
        description="Where the card draws the section: always, only while nothing is "
        "saved (`setup`, for what a first setup offers once), or only once something "
        "is (`panel`).",
        json_schema_extra={"role": "conditions"},
    )

    def choices(self) -> tuple[Option, ...]:
        """The values the admin picks among, none for a section without a list."""
        return ()

    def holds_several(self) -> bool:
        """True when the section keeps several of its choices at once."""
        return False

    def adds_to(self) -> str:
        """The list the section adds an item to, none for most sections."""
        return ""

    def opened_modal(self) -> ModalSpec | None:
        """The modal the section opens, None for one that opens none."""
        return None

    def inputs(self) -> tuple[TextField, ...]:
        """The inputs of the section's modal, none for most sections."""
        return ()

    def checked_by(self) -> Extension | None:
        """The validator a submitted modal of the section runs, None mostly."""
        return None

    def uploads(self) -> bool:
        """True when the section's modal uploads a file."""
        return False

    def normalizers(self) -> tuple[Extension | None, ...]:
        """The normalizer of each text the section's modal types, in its order."""
        return ()

    def validators(self) -> tuple[Extension, ...]:
        """Every validator the section names."""
        checked = self.checked_by()
        return (checked,) if checked else ()

    def offered_designs(self) -> tuple[Design, ...]:
        """The designs a gallery offers, none for any other section."""
        return ()

    def drawn_over(self) -> str:
        """The answer the design previews are drawn over, when the section says."""
        return ""

    def looked_up(self) -> Mapping[str, str]:
        """The values of the section's lookup it keeps as answers."""
        return {}

    def filled(self) -> Fills | None:
        """The copy the section writes into another key, None for most sections."""
        return None

    def picks(self) -> tuple[Select, ...]:
        """The native selects the section's picker holds, none for most sections."""
        return ()

    def controls_drawn(self) -> int:
        """How many components the section's controls take on its card."""
        return 1 + (2 if self.keys.mode else 1)

    def pictured(self) -> bool:
        """True when the section can draw a picture under its value."""
        return bool(self.image or self.keys.image or self.offered_designs())

    def picks_members(self) -> bool:
        """True when the section's value is a member picked on a member select."""
        return False

    def holds(self) -> tuple[str, ...]:
        """The keys of the card's sections it draws on its own screen, none mostly."""
        return ()

    def value_style(self) -> str | None:
        """The style the section's value reads and is saved in, None for as it is."""
        return None

    def pressed_label(self) -> Text | None:
        """The label of the button that opens the section, None for the default."""
        return None

    def select_placeholder(self) -> Text | None:
        """The hint of the section's select on the card, None for the default."""
        return None

    def own_screen_title(self) -> Text | None:
        """The title of the section's own screen, None for its label."""
        return None

    def own_screen_description(self) -> Text | None:
        """The intro of the section's own screen, None for none."""
        return None

    def drawn(self, saved: bool) -> bool:
        """True when the section belongs on a card whose answers are `saved` or not."""
        if self.appears_in == "setup":
            return not saved
        if self.appears_in == "panel":
            return saved
        return True


class PressedSection(SectionBase):
    """A section drawn with a button that opens what it edits."""

    button: ButtonSpec | None = Field(
        None,
        description="The button that opens the section, Edit when left out.",
        json_schema_extra={"role": "structure"},
    )

    def pressed_label(self) -> Text | None:
        """The declared button label."""
        return self.button.label if self.button else None


class ScreenSection(SectionBase):
    """A section with a screen of its own: its picker or the sections it groups."""

    screen: Screen | None = Field(
        None,
        description="The title and the intro of the section's own screen; its label "
        "and no intro when left out.",
        json_schema_extra={"role": "structure"},
    )

    def own_screen_title(self) -> Text | None:
        """The declared title of the section's own screen."""
        return self.screen.title if self.screen else None

    def own_screen_description(self) -> Text | None:
        """The declared intro of the section's own screen."""
        return self.screen.description if self.screen else None


class PlaceheldSection(SectionBase):
    """A section drawn as a select of its choices on the card."""

    placeholder: Text | None = Field(
        None,
        description="The grey hint of the section's select on the card; the title of "
        "its own screen, or its label, when left out.",
        json_schema_extra={"role": "copy"},
    )

    def select_placeholder(self) -> Text | None:
        """The declared hint of the section's select."""
        return self.placeholder


class TitleContentSection(PressedSection):
    """A title plus a body, default or custom."""

    type: Literal["title_content"] = Field(
        description="Draws a title and a body the admin keeps default or customizes.",
        json_schema_extra={"role": "identity"},
    )
    modal: TitleContentModal = Field(
        description="The modal the title and body are typed in.",
        json_schema_extra={"role": "structure"},
    )
    default: str = Field(
        description="The copy block whose `title` and `description` the section shows, "
        "and its modal starts from, until the admin customizes them: the text the "
        "command posts while nobody did.",
        examples=["<command>.ui.announcement.defaults"],
        json_schema_extra={"role": "copy"},
    )

    def opened_modal(self) -> TitleContentModal:
        """The modal that types the title and the body."""
        return self.modal

    def normalizers(self) -> tuple[Extension | None, ...]:
        """The modal's normalizer, over the title and over the body."""
        return (self.modal.normalize, self.modal.normalize)


class FileUploadSection(PressedSection):
    """An image, default or uploaded."""

    type: Literal["file_upload"] = Field(
        description="Draws an image the admin keeps default or uploads.",
        json_schema_extra={"role": "identity"},
    )
    modal: ModalSpec = Field(
        description="The modal the image is uploaded on.",
        json_schema_extra={"role": "structure"},
    )
    validation: Extension | None = Field(
        None,
        description="Runs a registered validator over the uploaded file's size, "
        "pixels and type before Keiko keeps it, and refuses the file it rejects.",
        json_schema_extra={"role": "behavior"},
    )

    def opened_modal(self) -> ModalSpec:
        """The modal that uploads the image."""
        return self.modal

    def checked_by(self) -> Extension | None:
        """The validator the uploaded file goes through."""
        return self.validation

    def uploads(self) -> bool:
        """An image section uploads its file."""
        return True


class ChannelSelectSection(ScreenSection):
    """One channel chosen on a native select."""

    type: Literal["channel_select"] = Field(
        description="Draws one channel picked on a native select.",
        json_schema_extra={"role": "identity"},
    )

    def controls_drawn(self) -> int:
        """The channel select and its row."""
        return 2

    def value_style(self) -> str:
        """A channel reads as its mention."""
        return "channel"


class UserSelectSection(ScreenSection):
    """One member chosen on a native member select, the member's picture under it."""

    type: Literal["user_select"] = Field(
        description="Draws one member picked on a native member select.",
        json_schema_extra={"role": "identity"},
    )
    avatar: bool = Field(
        False,
        description="Draws the picked member's avatar under the select, for an item "
        "that is about a member.",
        json_schema_extra={"role": "visual"},
    )

    def controls_drawn(self) -> int:
        """The member select and its row."""
        return 2

    def pictured(self) -> bool:
        """True when the section draws the chosen member's picture."""
        return self.avatar or bool(self.image)

    def picks_members(self) -> bool:
        """The value is a member."""
        return True

    def value_style(self) -> str:
        """A member reads as their mention."""
        return "user"


class ValueSelectSection(PlaceheldSection, ScreenSection):
    """One value chosen on a select of declared options."""

    type: Literal["value_select"] = Field(
        description="Draws one value picked on a select of the declared options.",
        json_schema_extra={"role": "identity"},
    )
    options: tuple[Option, ...] = Field(
        description="The values to pick among.",
        json_schema_extra={"role": "structure"},
    )
    dependents: tuple[ResetRule, ...] = Field(
        (),
        description="Keys a change clears when their check fails with the new value, "
        "such as a day the new month does not have.",
        json_schema_extra={"role": "behavior"},
    )

    def choices(self) -> tuple[Option, ...]:
        """The declared options."""
        return self.options

    def validators(self) -> tuple[Extension, ...]:
        """The validators that decide which dependent keys a change clears."""
        return tuple(rule.validation for rule in self.dependents)

    def controls_drawn(self) -> int:
        """The options select and its row."""
        return 2


class ButtonOptionsSection(PlaceheldSection, ScreenSection):
    """One value chosen on a row of buttons."""

    type: Literal["button_options"] = Field(
        description="Draws one value picked on a row of buttons.",
        json_schema_extra={"role": "identity"},
    )
    options: tuple[Option, ...] = Field(
        description="The values to pick among.",
        json_schema_extra={"role": "structure"},
    )

    def choices(self) -> tuple[Option, ...]:
        """The declared options."""
        return self.options

    def controls_drawn(self) -> int:
        """The options select and its row."""
        return 2


class BooleanToggleSection(SectionBase):
    """A yes or no chosen on two buttons."""

    type: Literal["boolean_toggle"] = Field(
        description="Draws a yes or no picked on two buttons.",
        json_schema_extra={"role": "identity"},
    )

    def controls_drawn(self) -> int:
        """The yes and no buttons and their row."""
        return 3

    def value_style(self) -> str:
        """A yes or no reads as one."""
        return "boolean"


class ModalInputSection(PressedSection):
    """One text typed into a modal."""

    type: Literal["modal_input"] = Field(
        description="Draws a text the admin types into a modal.",
        json_schema_extra={"role": "identity"},
    )
    format: ValueFormat | None = Field(
        None,
        description="How the typed value reads on the card and on summaries, as it "
        "was typed when left out.",
        json_schema_extra={"role": "visual"},
    )
    modal: InputsModal = Field(
        description="The modal the value is typed in.",
        json_schema_extra={"role": "structure"},
    )
    fills: Fills | None = Field(
        None,
        description="Writes the accepted value into another key's text, for a name "
        "typed once that the messages repeat.",
        json_schema_extra={"role": "behavior"},
    )
    lookup_answers: Open[str] = _open(
        "hidden answer keys -> service.field of the lookup",
        "Keeps values the modal's lookup found as answers a later line or picture "
        "reads.",
        role="data",
    )

    def opened_modal(self) -> InputsModal:
        """The modal its inputs are typed in."""
        return self.modal

    def inputs(self) -> tuple[TextField, ...]:
        """The modal's inputs."""
        return self.modal.inputs

    def checked_by(self) -> Extension | None:
        """The validator the modal runs on submit."""
        return self.modal.validation

    def normalizers(self) -> tuple[Extension | None, ...]:
        """The normalizer each input of the modal declares."""
        return tuple(field.normalize for field in self.modal.inputs)

    def looked_up(self) -> Mapping[str, str]:
        """The lookup values kept as answers, by answer key."""
        return self.lookup_answers

    def filled(self) -> Fills | None:
        """The copy the typed value writes into another key of the card."""
        return self.fills

    def value_style(self) -> str | None:
        """The style of the declared format of the typed value."""
        return stored_style(self.format)


class MultiSelectSection(PlaceheldSection, ScreenSection):
    """Several values chosen on a select of declared options."""

    type: Literal["multi_select"] = Field(
        description="Draws several values picked on a select of the declared options.",
        json_schema_extra={"role": "identity"},
    )
    options: tuple[Option, ...] = Field(
        description="The values to pick among.",
        json_schema_extra={"role": "structure"},
    )
    format: Literal["bullet", "code"] = Field(
        "bullet",
        description="How each picked value reads in the list: as it is, or in code "
        "like a link the admin typed.",
        json_schema_extra={"role": "visual"},
    )

    def choices(self) -> tuple[Option, ...]:
        """The declared options."""
        return self.options

    def holds_several(self) -> bool:
        """A multi-select keeps several options."""
        return True

    def controls_drawn(self) -> int:
        """The options select and its row."""
        return 2

    def value_style(self) -> str:
        """Several values read as a bulleted list, each in code when declared."""
        return self.format


class SelectsSection(ScreenSection):
    """Channels or roles picked on native selects, every select on one screen."""

    type: Literal["selects"] = Field(
        description="Draws channels or roles picked on native selects on one screen.",
        json_schema_extra={"role": "identity"},
    )
    selects: tuple[Select, ...] = Field(
        min_length=1,
        description="The native selects, each writing its own key.",
        json_schema_extra={"role": "structure"},
    )

    def picks(self) -> tuple[Select, ...]:
        """The declared selects, each writing its own key."""
        return self.selects

    def controls_drawn(self) -> int:
        """Each select and its row."""
        return 2 * len(self.selects)


class ListSection(SectionBase):
    """A list of the form drawn on the card: its items, Add and Remove."""

    type: Literal["list"] = Field(
        description="Draws a list of the form on the card, with Add and Remove.",
        json_schema_extra={"role": "identity"},
    )
    list: str = Field(
        description="The list step whose items the card draws, adds and removes, for "
        "a list that belongs on the card of its settings.",
        json_schema_extra={"role": "structure"},
    )

    def adds_to(self) -> str:
        """The list step the section's Add opens an item of."""
        return self.list

    def controls_drawn(self) -> int:
        """Add and Remove and their row."""
        return 3


class GroupSection(PressedSection, ScreenSection):
    """Other sections of the card, drawn on a screen of their own behind a button."""

    type: Literal["group"] = Field(
        description="Draws a button that opens other sections on a screen of "
        "their own.",
        json_schema_extra={"role": "identity"},
    )
    sections: tuple[str, ...] = Field(
        min_length=1,
        description="The keys of the sections the group's screen draws instead of the "
        "card, no group among them.",
        json_schema_extra={"role": "structure"},
    )

    def holds(self) -> tuple[str, ...]:
        """The keys of the sections its screen draws."""
        return self.sections


class DesignSection(PlaceheldSection):
    """One design chosen on a gallery of previews."""

    type: Literal["design_select"] = Field(
        description="Draws a design picked on a gallery of previews.",
        json_schema_extra={"role": "identity"},
    )
    options: tuple[Design, ...] = Field(
        description="The designs to pick among, with their previews.",
        json_schema_extra={"role": "structure"},
    )
    background: str = Field(
        "",
        description="The answer the design previews are drawn over; the previews are "
        "drawn again when it changes.",
        json_schema_extra={"role": "behavior"},
    )

    def choices(self) -> tuple[Option, ...]:
        """The designs as options, labelled by their names."""
        return tuple(
            Option(
                label=design.label, value=design.value, description=design.description
            )
            for design in self.options
        )

    def controls_drawn(self) -> int:
        """The designs select and its row."""
        return 2

    def offered_designs(self) -> tuple[Design, ...]:
        """The declared designs."""
        return self.options

    def drawn_over(self) -> str:
        """The answer the previews are drawn over."""
        return self.background


Section = Annotated[
    Union[
        TitleContentSection,
        FileUploadSection,
        ChannelSelectSection,
        UserSelectSection,
        ValueSelectSection,
        ButtonOptionsSection,
        BooleanToggleSection,
        ModalInputSection,
        MultiSelectSection,
        SelectsSection,
        ListSection,
        GroupSection,
        DesignSection,
    ],
    Field(discriminator="type"),
]


class StepBase(Node):
    """What every step declares."""

    type: str = Field(
        description="The step type, which decides how the step draws and what it "
        "answers."
    )
    key: str = Field(
        description="The answer key the step produces, unique among the form's "
        "top-level steps.",
        json_schema_extra={"role": "identity"},
    )
    title: Text = Field(
        description="The step's heading on its screen, its summary rows and the "
        "pickers.",
        json_schema_extra={"role": "copy"},
    )
    description: Text = Field(
        description="The body under the title, with `${answer.<key>}`, "
        "`${plural.<key>|<one>|<many>}` and `${context.<name>}` tokens.",
        json_schema_extra={"role": "copy"},
    )
    footer: Text | None = Field(
        None,
        description="The footer of the step's screen; the shared footer when left "
        "out, none when `null`.",
        json_schema_extra={"role": "copy"},
    )
    emoji: str | None = Field(
        None,
        description="The step's emoji: leads its title, its rows on summaries and, on "
        "a card, its header.",
        json_schema_extra={"role": "visual"},
    )


class ContentStep(StepBase):
    """What every step between the intro and the review declares."""

    hidden: bool = Field(
        False,
        description="Keeps the answer off the review, the panel and the intro, for a "
        "question that only steers the flow.",
        json_schema_extra={"role": "conditions"},
    )
    appears_in: Appearance = Field(
        "everywhere",
        description="Where the step is asked: in the setup and on the panel, or only "
        "on the panel (`panel`, for a list filled after saving, left out of the setup "
        "and the intro); `setup` is refused, the panel edits every step.",
        json_schema_extra={"role": "conditions"},
    )
    when: When | None = Field(
        None,
        description="Asks the step only while the rule holds.",
        json_schema_extra={"role": "conditions"},
    )
    overrides: tuple[TextOverride, ...] = Field(
        (),
        description="A title and a description that replace the step's own while a "
        "rule holds, for a step worded after the mode in force; the first item whose "
        "rule holds wins, so an item that writes a description, and every item "
        "before it, names only earlier answers, and a later one may name the step's "
        "own.",
        json_schema_extra={"role": "conditions"},
    )

    @model_validator(mode="after")
    def _asked_on_the_panel(self) -> ContentStep:
        if self.appears_in == "setup":
            raise ValueError(
                "the panel edits every step: a step appears everywhere or on the panel"
            )
        return self


class IntroPanel(Node):
    """How the intro reads on the panel."""

    description: Text = Field(
        description="The intro of the panel, written as what the feature is doing.",
        json_schema_extra={"role": "copy"},
    )


class IntroStep(StepBase):
    """The first screen: what the feature does, then Confirm."""

    type: Literal["intro"] = Field(
        description="Draws the first screen: what the feature does, then Confirm.",
        json_schema_extra={"role": "identity"},
    )
    panel: IntroPanel | None = Field(
        None,
        description="How the intro reads on the panel; the step's description when "
        "left out.",
        json_schema_extra={"role": "structure"},
    )


class TextStep(ContentStep):
    """A modal with one text input, checked before it is answered."""

    type: Literal["text"] = Field(
        description="Draws a modal with one text input, checked before it is answered.",
        json_schema_extra={"role": "identity"},
    )
    label: Text | None = Field(
        None,
        description="The label of the input, the step title when left out.",
        json_schema_extra={"role": "copy"},
    )
    placeholder: Text | None = Field(
        None,
        description="The grey hint inside the empty input.",
        json_schema_extra={"role": "copy"},
    )
    max_length: int = Field(
        40,
        description="The most characters the input accepts.",
        json_schema_extra={"role": "behavior"},
    )
    normalize: Extension | None = Field(
        None,
        description="The registered normalizer the typed value goes through before it "
        "is checked, looked up and kept.",
        json_schema_extra={"role": "behavior"},
    )
    validation: Extension | None = Field(
        None,
        description="Runs a registered validator on submit, with any lookup it needs.",
        json_schema_extra={"role": "behavior"},
    )
    transform: Extension | None = Field(
        None,
        description="Stores the typed value through a registered transform, such as a "
        "normalized link.",
        json_schema_extra={"role": "behavior"},
    )
    format: ValueFormat | None = Field(
        None,
        description="How summaries format the answer, such as a link shown as code.",
        json_schema_extra={"role": "visual"},
    )
    image: str = Field(
        "",
        description="The lookup answer whose picture the review draws under the step's "
        "line, such as the avatar of the account the lookup found.",
        json_schema_extra={"role": "visual"},
    )
    lookup_answers: Open[str] = _open(
        "hidden answer keys -> service.field of the lookup",
        "Keeps values the validator's lookup found as answers a later description, "
        "line or picture reads.",
        role="data",
    )

    def value_style(self) -> str | None:
        """The style of the declared format of the answer."""
        return stored_style(self.format)


class SingleChoiceStep(ContentStep):
    """Buttons to pick one option from."""

    type: Literal["single_choice"] = Field(
        description="Draws buttons to pick one option from.",
        json_schema_extra={"role": "identity"},
    )
    options: tuple[Option, ...] = Field(
        min_length=1,
        description="One button per option.",
        json_schema_extra={"role": "structure"},
    )
    required: bool = Field(
        False,
        description="Refuses Confirm while nothing is chosen.",
        json_schema_extra={"role": "behavior"},
    )
    auto_confirm: bool = Field(
        False,
        description="A press answers at once, with no Confirm button.",
        json_schema_extra={"role": "behavior"},
    )
    format: ValueFormat | None = Field(
        None,
        description="How summaries format the answer; the chosen option's label when "
        "left out.",
        json_schema_extra={"role": "visual"},
    )

    def value_style(self) -> str | None:
        """The style of the declared format of the answer."""
        return stored_style(self.format)


def owned_keys(section: Section) -> tuple[str, ...]:
    """The state keys a section writes, its modal's keyed inputs and its selects."""
    keys = list(section.keys.written())
    keys += [select.key for select in section.picks() if select.key not in keys]
    keys += [
        field.key for field in section.inputs() if field.key and field.key not in keys
    ]
    return tuple(keys)


class Required(Node):
    """Keys that must hold a value: every one of some, or any one of others."""

    all: tuple[str, ...] = Field(
        (),
        description="Refuses while one of these keys is shown and empty.",
        json_schema_extra={"role": "behavior"},
    )
    any: tuple[str, ...] = Field(
        (),
        description="Refuses while every one of these shown keys is empty, for "
        "settings any one of which is enough.",
        json_schema_extra={"role": "behavior"},
    )

    def named(self) -> tuple[str, ...]:
        """Every key the rule names, `all` first."""
        return (*self.all, *self.any)


class CardPreview(Node):
    """The Preview of a card that waits on keys or fills tokens."""

    required: Required = Field(
        default_factory=Required,
        description="Keys the preview is drawn from: while they are empty, Preview "
        "answers `error` instead.",
        json_schema_extra={"role": "behavior"},
    )
    error: str = Field(
        "",
        description="The error block whose message Preview answers while a key it "
        "requires is empty.",
        examples=["<command>.ui.errors.preview_needs_<key>"],
        json_schema_extra={"role": "copy"},
    )
    tokens: Open[PreviewToken] = _open(
        "preview tokens -> an answer or a context value",
        "Fills these `{token}` names in the previews of the messages the admin types, "
        "such as the member, the server and the date.",
        role="data",
    )

    @model_validator(mode="after")
    def _written_as_needed(self) -> CardPreview:
        waits = bool(self.required.named())
        if waits != bool(self.error):
            raise ValueError(
                "a preview that requires keys names its error, and only then"
            )
        if not waits and not self.tokens:
            raise ValueError("a preview that waits on nothing is written `true`")
        return self


class CardStep(ContentStep):
    """A Components V2 card whose sections each edit part of the answer."""

    type: Literal["card"] = Field(
        description="Draws a card whose sections each edit part of the answer.",
        json_schema_extra={"role": "identity"},
    )
    title: Text = _filled(
        "The card's heading on its screen, its summary rows and the pickers; a "
        "top-level card left without one reads the intro's, a card inside a list "
        "writes it.",
        role="copy",
    )
    header: Header | None = Field(
        None,
        description="The card's title and the picture of Keiko beside it.",
        json_schema_extra={"role": "visual"},
    )
    edit: Literal["card", "field"] = Field(
        "card",
        description="What one Edit opens on the panel and the review: the whole card, "
        "or only the section of each line (`field`, refused inside a list).",
        json_schema_extra={"role": "visual"},
    )
    preview: CardPreview | bool = Field(
        False,
        description="`true` draws a Preview first under the card, and beside each item "
        "of a list for an item card; the object for a preview that waits on keys or "
        "fills tokens.",
        json_schema_extra={"role": "visual"},
    )
    required: Required = Field(
        default_factory=Required,
        description="Keys that refuse Done while they are empty.",
        json_schema_extra={"role": "behavior"},
    )
    transform: Extension | None = Field(
        None,
        description="Writes a value derived from the card's state on Done and seeds "
        "its parts back on an edit, such as a date from a day and a month.",
        json_schema_extra={"role": "behavior"},
    )
    group: str | None = Field(
        None,
        description="The `groups` entry whose heading lists the card's rows.",
        json_schema_extra={"role": "structure"},
    )
    fields: tuple[CardField, ...] = Field(
        (),
        description="The keys Done saves, and how summaries and the intro show them.",
        json_schema_extra={"role": "data"},
    )
    sections: tuple[Section, ...] = Field(
        description="The card's editable parts.",
        json_schema_extra={"role": "structure"},
    )
    defaults: Open[Scalar | Text] = _open(
        "state keys of the card -> a value or a text",
        "Seeds the card's state with these values while nothing is answered or saved.",
        role="behavior",
    )

    @model_validator(mode="after")
    def _titled(self) -> CardStep:
        if self.title is None:
            raise ValueError("a card inside a list writes its title")
        return self

    def previewed(self) -> bool:
        """True when the card draws a Preview button."""
        return self.preview is not False

    def preview_spec(self) -> CardPreview | None:
        """The preview's keys, error and tokens, None for `true` or `false`."""
        return self.preview if isinstance(self.preview, CardPreview) else None

    def preview_tokens(self) -> Mapping[str, PreviewToken]:
        """The tokens the previews of the card's messages fill."""
        spec = self.preview_spec()
        return spec.tokens if spec else {}

    def state_keys(self) -> tuple[str, ...]:
        """Every state key the sections own, in declaration order."""
        keys: list[str] = []
        for section in self.sections:
            for key in owned_keys(section):
                if key not in keys:
                    keys.append(key)
        return tuple(keys)

    def _owners(self) -> dict[str, tuple[Text, str | None]]:
        owners: dict[str, tuple[Text, str | None]] = {}
        for section in self.sections:
            for select in section.picks():
                owners.setdefault(select.key, (select.label, select.value_style()))
            for key in section.keys.written():
                shown = section.value_style() if key == section.keys.value else None
                owners.setdefault(key, (section.label, shown))
            for field in section.inputs():
                if field.key:
                    owners.setdefault(field.key, (field.label, None))
        return owners

    def label_of(self, field: CardField) -> Text:
        """The field's label, or the label of what owns its key."""
        return field.label or self._owners()[field.key][0]

    def style_of(self, field: CardField) -> str | None:
        """The style of the field's format, or the style of what owns its key."""
        if field.format is not None:
            return stored_style(field.format)
        owner = self._owners().get(field.key)
        return owner[1] if owner else None

    def unlabelled(self) -> tuple[str, ...]:
        """The fields that write no label over a key nothing on the card owns."""
        owners = self._owners()
        return tuple(
            field.key
            for field in self.fields
            if field.label is None and field.key not in owners
        )

    def header_title(self) -> Text:
        """The header's title, or the card's own."""
        return self.header.title if self.header and self.header.title else self.title


class Unique(Node):
    """The item key two items of a list may not share."""

    key: str = Field(
        description="The item key two items may not share, which also names an item.",
        json_schema_extra={"role": "data"},
    )
    immutable: bool = Field(
        False,
        description="Draws the key's value of an edited item as a line with no "
        "control, so the member an item is about stays fixed.",
        json_schema_extra={"role": "behavior"},
    )


class ItemsSpec(Node):
    """How many items a list holds and what tells them apart."""

    max: int = Field(
        description="The most items the list holds; Add is off when full.",
        json_schema_extra={"role": "behavior"},
    )
    min: int = Field(
        0,
        ge=0,
        le=1,
        description="`1` keeps the list from being emptied: Remove leaves the last "
        "item, or warns that the feature turns off with it.",
        json_schema_extra={"role": "behavior"},
    )
    unique: Unique | None = Field(
        None,
        description="The item key two items may not share.",
        json_schema_extra={"role": "behavior"},
    )

    def unique_key(self) -> str | None:
        """The item key two items may not share, None when items may repeat."""
        return self.unique.key if self.unique else None


class Search(Node):
    """How an admin finds an item of a list by its member."""

    screen: Screen | None = Field(
        None,
        description="The title of a found item's screen, with `$item`; the list's "
        "emoji and the item's name when left out.",
        json_schema_extra={"role": "structure"},
    )

    @model_validator(mode="after")
    def _titled_only(self) -> Search:
        if self.screen is not None and self.screen.description is not None:
            raise ValueError("a found item's screen draws no description")
        return self

    def title(self) -> Text | None:
        """The declared title of a found item's screen."""
        return self.screen.title if self.screen else None


class ReviewStep(StepBase):
    """The last screen: the summary and the final Confirm."""

    type: Literal["review"] = Field(
        description="Draws the last screen: the summary and the final Confirm.",
        json_schema_extra={"role": "identity"},
    )
    title: Text = _filled(
        "The review's heading; the intro's title when left out.",
        role="copy",
    )
    description: Text = _filled(
        "The body under the title, with `${answer.<key>}`, "
        "`${plural.<key>|<one>|<many>}` and `${context.<name>}` tokens; the shared "
        "`fallbacks.review_description` when left out.",
        role="copy",
    )
    preview: bool = Field(
        False,
        description="Shows a Preview on the review while no list item previews itself.",
        json_schema_extra={"role": "visual"},
    )
    info: Text | None = Field(
        None,
        description="A closing note above the footer, such as how members add their "
        "own items.",
        json_schema_extra={"role": "copy"},
    )
    lines: tuple[ReviewLine, ...] = Field(
        (),
        description="Lines read from answers the document never keeps, such as what a "
        "lookup found.",
        json_schema_extra={"role": "data"},
    )

    @model_validator(mode="after")
    def _written_out(self) -> ReviewStep:
        if self.title is None or self.description is None:
            raise ValueError("a review reads its title from an intro")
        return self


class ListStep(ContentStep):
    """A list of items, each built by a child session over `steps`."""

    type: Literal["list"] = Field(
        description="Draws a list of items, each built by a child session over "
        "`steps`.",
        json_schema_extra={"role": "identity"},
    )
    items: ItemsSpec = Field(
        description="How many items the list holds and how.",
        json_schema_extra={"role": "structure"},
    )
    edit: Literal["list", "item"] = Field(
        "list",
        description="What one Edit opens on the panel and the review: the whole list, "
        "or one item, each drawn as its own block with Edit and Preview (`item`).",
        json_schema_extra={"role": "visual"},
    )
    remove: Literal["list", "item"] = Field(
        "list",
        description="Where items are removed: on the list's own screen, or with a red "
        "Remove beside each item on the panel (`item`, needs `edit: item`).",
        json_schema_extra={"role": "visual"},
    )
    search: Search | None = Field(
        None,
        description="Adds a Search that finds an item by the member its `unique.key` "
        "picks, for a list keyed by members.",
        json_schema_extra={"role": "behavior"},
    )
    counter: Text | None = Field(
        None,
        description="The value beside the list's title on the panel and the review, "
        "with `$count` and `$max`.",
        json_schema_extra={"role": "copy"},
    )
    last_item_warning: Text | None = Field(
        None,
        description="The warning before removing the last item of a list with "
        "`items.min: 1`, with `$item` and `$feature`.",
        json_schema_extra={"role": "copy"},
    )
    group: str | None = Field(
        None,
        description="The `groups` entry whose heading lists the list's rows.",
        json_schema_extra={"role": "structure"},
    )
    steps: tuple[Step, ...] = Field(
        description="The item's own steps, run as a child session for each item.",
        json_schema_extra={"role": "structure"},
    )


Step = Annotated[
    Union[IntroStep, TextStep, SingleChoiceStep, CardStep, ListStep, ReviewStep],
    Field(discriminator="type"),
]

STEP_TYPES: tuple[str, ...] = (
    "intro",
    "text",
    "single_choice",
    "card",
    "list",
    "review",
)
SILENT = ("intro", "review")


def listed_on_cards(steps: Sequence[Step]) -> frozenset[str]:
    """The lists a card among `steps` draws and adds to, which a setup never walks."""
    return frozenset(
        section.adds_to()
        for step in steps
        if isinstance(step, CardStep)
        for section in step.sections
        if section.adds_to()
    )


class FormFile(Node):
    """What a command's form.yml declares."""

    version: int = Field(
        1,
        description="The definition version a session is pinned to; every form is "
        "at 1 until one needs two live versions.",
        json_schema_extra={"role": "identity"},
    )
    steps: tuple[Step, ...] = Field(
        description="The form's screens in order, the intro first and the review last.",
        json_schema_extra={"role": "structure"},
    )
    groups: Open[PanelGroupSpec] = _open(
        "group ids -> a panel group",
        "Headings that steps and card fields name with `group` to share them on the "
        "panel and the review.",
        role="structure",
    )


class FormDefinition(FormFile):
    """A compiled, versioned form: the only thing the engine reads at runtime."""

    key: str = Field(description="The form key, the command folder of its form.yml.")

    def background(self) -> str:
        """The answer the design previews are drawn over, when one is declared."""
        for step in self.steps:
            if not isinstance(step, CardStep):
                continue
            for section in step.sections:
                if section.drawn_over():
                    return section.drawn_over()
        return ""

    def designs(self) -> tuple[Design, ...]:
        """Every design a gallery of this form offers, in declaration order."""
        found: list[Design] = []
        for step in self.steps:
            if isinstance(step, CardStep):
                for section in step.sections:
                    found += section.offered_designs()
        return tuple(found)

    def step(self, key: str) -> Step:
        """The top-level step with `key`."""
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(key)

    def index_of(self, key: str) -> int:
        """The position of the top-level step with `key`."""
        for index, step in enumerate(self.steps):
            if step.key == key:
                return index
        raise KeyError(key)

    @property
    def composition(self) -> ListStep | None:
        """The list step, when the form has one."""
        for step in self.steps:
            if isinstance(step, ListStep):
                return step
        return None

    @property
    def items(self) -> ItemsSpec | None:
        """The item limits of the list step, when the form has one."""
        composition = self.composition
        return composition.items if composition else None


TextOverride.model_rebuild()
SectionBase.model_rebuild()
ListStep.model_rebuild()
FormDefinition.model_rebuild()


class Fallbacks(Node):
    """The copy a step reads in place of a field it leaves out."""

    footer: Text = Field(
        description="The footer of every step that writes no `footer`, at any depth, "
        "and the closing line of every embed and card that writes none.",
        json_schema_extra={"role": "copy"},
    )
    review_description: Text = Field(
        description="The description of every review that writes none, naming the "
        "command it turns on with `${context.command}`.",
        json_schema_extra={"role": "copy"},
    )


class Vocabulary(Node):
    """The copy every form shares: its fallbacks and the texts `${ref.<name>}` names."""

    fallbacks: Fallbacks = Field(
        description="The texts every form falls back to when a step leaves one out.",
        json_schema_extra={"role": "structure"},
    )
    texts: Open[Text] = _open(
        "ref names -> a text",
        "The texts a form or a copy file names with `${ref.<name>}` wherever a text "
        "is allowed.",
        role="copy",
    )


def vocabulary_from(path: Path) -> Vocabulary:
    """The vocabulary a file at `path` declares, read as strictly as a form, each
    `$name` and `${context...}` of its texts declared."""
    name = str(path)
    loaded = with_context(strict_yaml(path.read_text(encoding="utf-8"), name), name)
    for where, words in written_texts(loaded):
        for token in tokens(words):
            problem = _fact_problem(token) or _context_problem(token)
            if problem:
                raise TextError(name, where, problem)
    return Vocabulary.model_validate(loaded)


@cache
def shared_vocabulary() -> Vocabulary:
    """The vocabulary of `app/forms/vocabulary.yml`, read once."""
    return vocabulary_from(Path(__file__).parents[1] / "vocabulary.yml")


CV2_MAX_COMPONENTS = 40


class CompileError(Exception):
    """A definition that cannot become a form, with where and why."""

    def __init__(self, form: str, step_key: str | None, field: str, reason: str):
        self.form = form
        self.step_key = step_key
        self.field = field
        self.reason = reason
        where = f"{form}.{step_key}" if step_key else form
        super().__init__(f"{where}: {field}: {reason}")


def compile_form(
    key: str, raw: Mapping[str, Any], vocabulary: Vocabulary | None = None
) -> FormDefinition:
    """Write out, validate and check `raw`: the definition of `key` or a refusal."""
    if "key" in raw:
        raise CompileError(key, None, "key", "unknown field, the key is the folder")
    payload = {**written_out(key, raw, vocabulary), "key": key}
    definition = _validated(key, payload)

    _check_shape(definition)
    _check_scope(definition, definition.steps, outer=())
    _check_tokens(key, payload, definition)
    _check_panel_groups(definition)
    _check_registries(definition)
    _check_limits(definition)
    shown = finished_texts(payload)
    return definition if shown == payload else _validated(key, shown)


def _validated(key: str, payload: Mapping[str, Any]) -> FormDefinition:
    try:
        return FormDefinition.model_validate(payload)
    except ValidationError as error:
        raise _as_compile_error(key, error) from None


def written_out(
    key: str, raw: Mapping[str, Any], vocabulary: Vocabulary | None = None
) -> Mapping[str, Any]:
    """The raw definition of `key` with what its texts read at load written in (each
    `${ref.<name>}` and each command and group of `${context...}`) and every
    fallback written."""
    shared = vocabulary if vocabulary is not None else shared_vocabulary()
    try:
        loaded = with_context(raw, "", shared.texts)
    except TextError as error:
        step_key, field = _within_step(raw, error.where)
        raise CompileError(key, step_key, field, error.problem) from None
    steps = loaded.get("steps")

    if not isinstance(steps, list) or not steps:
        raise CompileError(key, None, "steps", "a form declares a non-empty steps list")
    intro = steps[0].get("title") if steps[0].get("type") == "intro" else None
    _fill_fallbacks(steps, shared.fallbacks, intro)
    return dict(loaded)


def _within_step(raw: Mapping[str, Any], where: str) -> tuple[str | None, str]:
    step_key: str | None = None
    node: Any = raw
    while where.startswith("steps[") and isinstance(node, Mapping):
        index, _bracket, rest = where[len("steps[") :].partition("]")
        found = node.get("steps")
        if not index.isdigit() or not isinstance(found, list):
            break
        node = found[int(index)] if int(index) < len(found) else {}
        named = node.get("key") if isinstance(node, Mapping) else None
        step_key = str(named) if named else step_key
        where = rest.removeprefix(".")
    return step_key, where


def _as_compile_error(form: str, error: ValidationError) -> CompileError:
    first = error.errors()[0]
    location = [str(part) for part in first["loc"]]
    step_key = None
    if len(location) >= 2 and location[0] == "steps" and location[1].isdigit():
        step_key = f"steps[{location[1]}]"
    field = ".".join(location) or "definition"
    reason = first["msg"]
    if first["type"] == "extra_forbidden":
        reason = "unknown field"
    return CompileError(form, step_key, field, reason)


def _fill_fallbacks(
    steps: Sequence[dict[str, Any]], fallbacks: Fallbacks, intro: Any
) -> None:
    for step in steps:
        kind = step.get("type")
        step.setdefault("footer", fallbacks.footer.model_dump(by_alias=True))
        if kind == "review":
            described = fallbacks.review_description.model_dump(by_alias=True)
            step.setdefault("description", described)
        if intro is not None and kind in ("card", "review"):
            step.setdefault("title", intro)
        if kind == "list" and isinstance(step.get("steps"), list):
            _fill_fallbacks(step["steps"], fallbacks, None)


def _check_shape(definition: FormDefinition) -> None:
    steps = definition.steps
    if steps[0].type != "intro":
        raise CompileError(
            definition.key, steps[0].key, "type", "the first step is intro"
        )
    if steps[-1].type != "review":
        raise CompileError(
            definition.key, steps[-1].key, "type", "the last step is review"
        )
    seen: set[str] = set()
    for step in steps:
        if step.key in seen:
            raise CompileError(definition.key, step.key, "key", "duplicated step key")
        seen.add(step.key)


def produced_keys(step: Step) -> tuple[str, ...]:
    """The answer keys a step writes: its own plus its card and lookup keys."""
    keys = [step.key]
    if isinstance(step, TextStep):
        keys += list(step.lookup_answers)
    if isinstance(step, CardStep):
        keys += [key for key in step.state_keys() if key not in keys]
        keys += [field.key for field in step.fields if field.key not in keys]
    return tuple(keys)


def options_for(steps: Sequence[Step], key: str) -> tuple[Option, ...] | None:
    """The declared options behind `key`, when the producing step has them."""
    for step in steps:
        if isinstance(step, SingleChoiceStep) and step.key == key:
            return step.options
        if isinstance(step, CardStep):
            for section in step.sections:
                choices = section.choices()
                if choices and section.keys.value == key:
                    return choices
    return None


def _check_leaf(
    form: str,
    step_key: str | None,
    leaf: Leaf,
    scopes: Sequence[tuple[tuple[str, ...], Sequence[Step]]],
) -> None:
    key = leaf.key
    depth = 0

    while key.startswith("parent."):
        key = key[len("parent.") :]
        depth += 1
    if key.startswith("item.") or key == "items.count":
        return
    if depth >= len(scopes):
        raise CompileError(form, step_key, "when", f"{leaf.key!r} has no such scope")
    produced, steps = scopes[-1 - depth]
    if key not in produced:
        raise CompileError(
            form, step_key, "when", f"{leaf.key!r} is not produced earlier"
        )
    options = options_for(steps, key)
    values = list(leaf.in_ or ()) + list(leaf.not_in or ())

    if leaf.is_ is not None:
        values.append(leaf.is_)
    if options is None:
        return
    for value in values:
        if not any(same_value(value, option.value) for option in options):
            raise CompileError(
                form, step_key, "when", f"{value!r} is not an option of {key!r}"
            )


def _check_scope(
    definition: FormDefinition,
    steps: Sequence[Step],
    outer: Sequence[tuple[tuple[str, ...], Sequence[Step]]],
) -> None:
    produced: list[str] = []
    for step in steps:
        scopes = list(outer) + [(tuple(produced), steps)]
        saved = list(outer) + [(tuple(produced) + produced_keys(step), steps)]
        if isinstance(step, ContentStep):
            _check_rules(definition.key, step, scopes, saved)
        if isinstance(step, CardStep):
            _check_card(definition, step)
        if isinstance(step, ListStep):
            _check_scope(
                definition, step.steps, list(outer) + [(tuple(produced), steps)]
            )
        produced.extend(produced_keys(step))


def _check_rules(
    form: str,
    step: ContentStep,
    scopes: Sequence[tuple[tuple[str, ...], Sequence[Step]]],
    saved: Sequence[tuple[tuple[str, ...], Sequence[Step]]],
) -> None:
    for leaf in leaves(step.when):
        _check_leaf(form, step.key, leaf, scopes)
    described = [item.description is not None for item in step.overrides]
    last = len(described) - described[::-1].index(True) if any(described) else 0
    for index, item in enumerate(step.overrides):
        known = scopes if index < last else saved
        for leaf in leaves(item.when):
            _check_leaf(form, step.key, leaf, known)


def _check_tokens(
    form: str, payload: Mapping[str, Any], definition: FormDefinition
) -> None:
    every = tuple(key for step in definition.steps for key in produced_keys(step))
    root = {name: value for name, value in payload.items() if name != "steps"}
    _check_texts(form, None, root, every)
    _check_step_texts(form, payload["steps"], definition.steps)


def _check_step_texts(
    form: str, written: Sequence[Mapping[str, Any]], steps: Sequence[Step]
) -> None:
    produced: list[str] = []
    for raw, step in zip(written, steps):
        nested = isinstance(step, ListStep)
        own = {
            name: value for name, value in raw.items() if not nested or name != "steps"
        }
        _check_texts(form, step.key, own, (*produced, *produced_keys(step)))
        if isinstance(step, ListStep):
            _check_step_texts(form, raw["steps"], step.steps)
        produced.extend(produced_keys(step))


def _check_texts(
    form: str, step_key: str | None, node: Mapping[str, Any], known: Sequence[str]
) -> None:
    for path, words in written_texts(node):
        try:
            found = tokens(words)
        except TokenError as error:
            raise CompileError(form, step_key, path, str(error)) from None
        for token in found:
            problem = _token_problem(token, known)
            if problem:
                raise CompileError(form, step_key, path, problem)


def _token_problem(token: Token, known: Sequence[str]) -> str:
    if token.kind == "fact":
        return _fact_problem(token)
    if token.kind == "context":
        return _context_problem(token)
    if token.name not in known:
        closest = hint(token.name, known, lambda key: f"${{{token.kind}.{key}}}")
        return f"{token.written}: {token.name!r} is not produced earlier{closest}"
    if token.format and token.format not in TOKEN_FORMATS:
        closest = hint(token.format, TOKEN_FORMATS, lambda name: f":{name}")
        return (
            f"{token.written}: {token.format!r} is no format "
            f"({', '.join(TOKEN_FORMATS)}){closest}"
        )
    return ""


def _fact_problem(token: Token) -> str:
    names = global_catalog().facts
    if token.kind != "fact" or token.name in names:
        return ""
    return (
        f"{token.written} is no fact of app/core/global.yml"
        f"{fact_hint(token.name, names)}"
    )


def _context_problem(token: Token) -> str:
    if token.kind != "context" or context_entry(token.name) is not None:
        return ""
    closest = hint(
        token.name, global_catalog().context, lambda name: f"${{context.{name}}}"
    )
    return f"{token.written} is no context entry of app/core/global.yml{closest}"


def panel_group_of(step: Step) -> str | None:
    """The panel group a card or a list names, None for any other step."""
    if isinstance(step, (CardStep, ListStep)):
        return step.group
    return None


def _group_members(steps: Sequence[Step]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for step in steps:
        named = panel_group_of(step)
        if named:
            found.append((step.key, named))
        if isinstance(step, CardStep):
            found += [(step.key, field.group) for field in step.fields if field.group]
    return found


def _check_panel_groups(definition: FormDefinition) -> None:
    form, groups = definition.key, definition.groups
    named: set[str] = set()
    for step_key, member_of in _group_members(definition.steps):
        if member_of not in groups:
            raise CompileError(
                form,
                step_key,
                "group",
                f"{member_of!r} is no entry of groups",
            )
        named.add(member_of)
    for step in definition.steps:
        if isinstance(step, ListStep) and _group_members(step.steps):
            raise CompileError(form, step.key, "group", "a list item names no group")
    produced = tuple(key for step in definition.steps for key in produced_keys(step))
    for group_key, group in groups.items():
        if group_key not in named:
            raise CompileError(
                form, None, f"groups.{group_key}", "no step or field names it"
            )
        for item in group.overrides:
            for leaf in leaves(item.when):
                _check_leaf(form, None, leaf, [(produced, definition.steps)])


def _check_modal(definition: FormDefinition, card: CardStep, section: Section) -> None:
    fields = section.inputs()
    if len(fields) > DiscordLimits.MODAL_INPUTS:
        raise CompileError(
            definition.key,
            card.key,
            "modal",
            f"a modal holds at most {DiscordLimits.MODAL_INPUTS} inputs",
        )
    if any(field.key is None for field in fields) and not section.keys.value:
        raise CompileError(
            definition.key,
            card.key,
            "keys",
            "inputs without a key need keys.value",
        )
    keyed = [field.key for field in fields if field.key]

    if fields and len(keyed) == len(fields) and section.keys.value not in keyed:
        raise CompileError(
            definition.key,
            card.key,
            "keys",
            "keys.value names none of the inputs",
        )


def _check_lists(definition: FormDefinition, card: CardStep) -> None:
    lists = {step.key for step in definition.steps if isinstance(step, ListStep)}
    for section in card.sections:
        if section.adds_to() and section.adds_to() not in lists:
            raise CompileError(
                definition.key,
                card.key,
                "list",
                f"{section.adds_to()!r} is no list of the form",
            )


def _check_card(definition: FormDefinition, card: CardStep) -> None:
    _check_lists(definition, card)
    state_keys = set(card.state_keys())
    for section in card.sections:
        _check_modal(definition, card, section)
    for section in card.sections:
        for leaf in leaves(section.when):
            if leaf.key not in state_keys:
                raise CompileError(
                    definition.key, card.key, "when", f"{leaf.key!r} unknown"
                )
    _check_groups(definition, card)
    known = state_keys | {field.key for field in card.fields}
    spec = card.preview_spec()
    for name, keys in (
        ("required", card.required.named()),
        ("preview.required", spec.required.named() if spec else ()),
        ("defaults", tuple(card.defaults)),
    ):
        for key in keys:
            if key not in known:
                raise CompileError(definition.key, card.key, name, f"{key!r} unknown")
    for key in card.unlabelled():
        raise CompileError(
            definition.key,
            card.key,
            "fields",
            f"{key!r} is owned by no section, so it writes its own label",
        )
    _check_section_keys(definition, card, state_keys)


def _check_groups(definition: FormDefinition, card: CardStep) -> None:
    sections = {section.key: section for section in card.sections}
    for group in card.sections:
        for key in group.holds():
            held = sections.get(key)
            if held is None:
                raise CompileError(
                    definition.key, card.key, "sections", f"{key!r} is no section"
                )
            if held.holds():
                raise CompileError(
                    definition.key,
                    card.key,
                    "sections",
                    f"{key!r} is a group, and a group holds no group",
                )


def _check_section_keys(
    definition: FormDefinition, card: CardStep, state_keys: set[str]
) -> None:
    looked_up = {key for section in card.sections for key in section.looked_up()}
    for section in card.sections:
        if section.image and section.image not in state_keys | looked_up:
            raise CompileError(
                definition.key,
                card.key,
                "image",
                f"{section.image!r} is no state key or lookup answer of the card",
            )
        fills = section.filled()
        if fills is not None and fills.into not in state_keys:
            raise CompileError(
                definition.key,
                card.key,
                "fills",
                f"{fills.into!r} is no state key of the card",
            )


def _walk(steps: Sequence[Step]) -> list[Step]:
    found: list[Step] = []
    for step in steps:
        found.append(step)
        if isinstance(step, ListStep):
            found.extend(_walk(step.steps))
    return found


def _check_field_edit(definition: FormDefinition) -> None:
    for outer in definition.steps:
        if not isinstance(outer, ListStep):
            continue
        for inner in outer.steps:
            if isinstance(inner, CardStep) and inner.edit == "field":
                raise CompileError(
                    definition.key,
                    inner.key,
                    "edit",
                    "a card inside a list is edited whole, not by field",
                )


def _check_lookup_answers(definition: FormDefinition) -> None:
    for step in _walk(definition.steps):
        if not isinstance(step, TextStep):
            continue
        for path in step.lookup_answers.values():
            service, _, field = path.partition(".")
            if not service or not field or "." in field:
                raise CompileError(
                    definition.key,
                    step.key,
                    "lookup_answers",
                    "lookup_answers names service.field",
                )
        if step.image and step.image not in step.lookup_answers:
            raise CompileError(
                definition.key,
                step.key,
                "image",
                f"{step.image!r} is no lookup answer of the step",
            )


def _check_item_keys(definition: FormDefinition) -> None:
    for step in _walk(definition.steps):
        if not isinstance(step, ListStep):
            continue
        produced = {key for inner in step.steps for key in produced_keys(inner)}
        unique = step.items.unique_key() or ""
        if unique and unique not in produced:
            raise CompileError(
                definition.key, step.key, "unique.key", f"{unique!r} is no item key"
            )
        if step.search is not None and not picks_a_member(step.steps, unique):
            raise CompileError(
                definition.key,
                step.key,
                "search",
                "search finds an item by its member: unique.key is picked on a "
                "member select",
            )
        if step.remove == "item" and step.edit != "item":
            raise CompileError(
                definition.key,
                step.key,
                "remove",
                "a Remove beside each item needs edit: item",
            )
        if step.remove == "item" and step.items.min and not step.last_item_warning:
            raise CompileError(
                definition.key,
                step.key,
                "last_item_warning",
                "removing the last item of a required list turns it off: say so",
            )


def _check_registries(definition: FormDefinition) -> None:
    _check_field_edit(definition)
    _check_lookup_answers(definition)
    _check_item_keys(definition)
    for step in _walk(definition.steps):
        validations = list(_card_validations(step))
        if isinstance(step, TextStep) and step.validation:
            validations.append(step.validation)
        for validation in validations:
            registered = VALIDATORS.get(validation.name)
            declared = registered.params if registered else None
            _check_extension(
                definition.key,
                step.key,
                ("validation", "validator"),
                validation,
                declared,
            )
        transform = step.transform if isinstance(step, (TextStep, CardStep)) else None
        if transform:
            known = () if transform.name in TRANSFORMS else None
            _check_extension(
                definition.key, step.key, ("transform", "transform"), transform, known
            )
        for normalize in _normalizers(step):
            known = () if normalize.name in NORMALIZERS else None
            _check_extension(
                definition.key, step.key, ("normalize", "normalizer"), normalize, known
            )
        _check_uploads(definition.key, step)


def _check_uploads(form: str, step: Step) -> None:
    checked: list[tuple[bool, Extension]] = []
    if isinstance(step, TextStep) and step.validation:
        checked.append((False, step.validation))
    if isinstance(step, CardStep):
        checked += [
            (section.uploads(), validation)
            for section in step.sections
            for validation in section.validators()
        ]
    for uploads, validation in checked:
        reads_upload = "upload" in VALIDATORS[validation.name].needs
        if reads_upload and not uploads:
            raise CompileError(
                form, step.key, "validation", f"{validation.name} checks an upload"
            )
        if uploads and not reads_upload:
            raise CompileError(
                form,
                step.key,
                "validation",
                f"an upload is checked by a validator of uploads, not "
                f"{validation.name}",
            )


def _normalizers(step: Step) -> list[Extension]:
    found: list[Extension] = []
    if isinstance(step, TextStep) and step.normalize:
        found.append(step.normalize)
    if isinstance(step, CardStep):
        for section in step.sections:
            found += [rule for rule in section.normalizers() if rule]
    return found


def _check_extension(
    form: str,
    step_key: str,
    named: tuple[str, str],
    reference: Extension,
    declared: Sequence[str] | None,
) -> None:
    field, kind = named
    if declared is None:
        raise CompileError(form, step_key, field, f"unknown {kind} {reference.name!r}")
    if set(reference.params) != set(declared):
        raise CompileError(
            form,
            step_key,
            field,
            f"{reference.name} takes the params {sorted(declared)}",
        )


def _card_validations(step: Step) -> list[Extension]:
    if not isinstance(step, CardStep):
        return []
    validations: list[Extension] = []
    for section in step.sections:
        validations += section.validators()
    return validations


def _section_components(section: Section) -> int:
    return 2 + section.controls_drawn() + int(section.pictured())


def _held_components(card: CardStep, group: Section) -> int:
    return sum(
        _section_components(section)
        for section in card.sections
        if section.key in group.holds()
    )


def card_components(card: CardStep, back: bool = True) -> int:
    """How many components the card can take on Discord, each group at its most."""
    frame = 1 + 4 + 1 + 1
    held = {key for group in card.sections for key in group.holds()}
    sections = 0

    for section in card.sections:
        if section.key in held:
            continue
        drawn = _section_components(section)
        if section.holds():
            drawn = max(drawn, _held_components(card, section))
        sections += drawn
    buttons = 2 + int(back) + int(card.previewed())
    rows = -(-buttons // DiscordLimits.BUTTONS_PER_ROW)
    return frame + sections + rows + buttons


def group_components(card: CardStep, group: Section) -> int:
    """How many components a group's own screen can take: its sections and Back."""
    frame = 1 + 2 + 1 + 1
    return frame + _held_components(card, group) + 2


def cards_with_back(steps: Sequence[Step]) -> Iterator[tuple[CardStep, bool]]:
    """Every card of `steps` and of their lists, and whether Back may show on it."""
    for index, step in enumerate(steps):
        if isinstance(step, CardStep):
            yield step, any(before.type != "intro" for before in steps[:index])
        if isinstance(step, ListStep):
            yield from cards_with_back(step.steps)


def picks_a_member(steps: Sequence[Step], key: str) -> bool:
    """True when the step that produces `key` picks it on a member select."""
    return any(
        isinstance(step, CardStep)
        and any(
            section.picks_members() and key in owned_keys(section)
            for section in step.sections
        )
        for step in steps
    )


def _check_limits(definition: FormDefinition) -> None:
    for step in _walk(definition.steps):
        limit = ViewConstants.EDIT_BY_ITEM_MAX
        if isinstance(step, ListStep) and step.edit == "item":
            if step.items.max > limit:
                raise CompileError(
                    definition.key,
                    step.key,
                    "edit",
                    f"{step.items.max} items, at most {limit} items "
                    "can each carry an Edit",
                )
    for step, back in cards_with_back(definition.steps):
        screens = [("sections", card_components(step, back))]
        screens += [
            (f"{group.key}.sections", group_components(step, group))
            for group in step.sections
            if group.holds()
        ]
        for field, estimate in screens:
            if estimate > CV2_MAX_COMPONENTS:
                raise CompileError(
                    definition.key,
                    step.key,
                    field,
                    f"about {estimate} components, Discord allows {CV2_MAX_COMPONENTS}",
                )


class DefinitionRegistry:
    """The compiled forms of one source."""

    def __init__(self, source: DefinitionSource | None = None) -> None:
        self.source: DefinitionSource = source or DiskSource()
        self._definitions: dict[tuple[str, int], FormDefinition] = {}
        self._latest: dict[str, int] = {}

    def load_all(self) -> tuple[FormDefinition, ...]:
        """Compile every form the source lists; a bad one raises `CompileError`."""
        for key in self.source.list():
            self._register(compile_form(key, self.source.load(key)))
        return tuple(self._definitions.values())

    def _register(self, definition: FormDefinition) -> None:
        self._definitions[(definition.key, definition.version)] = definition
        self._latest[definition.key] = max(
            definition.version, self._latest.get(definition.key, 0)
        )

    def get(self, key: str, version: int | None = None) -> FormDefinition:
        """The definition of `key` at `version`, the latest when not given."""
        if key not in self._latest:
            self._register(compile_form(key, self.source.load(key)))
        return self._definitions[(key, version or self._latest[key])]

    def keys(self) -> tuple[str, ...]:
        """Every form key loaded so far."""
        return tuple(sorted(self._latest))


registry = DefinitionRegistry()

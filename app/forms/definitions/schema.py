"""The typed shape of a form definition.

Every model rejects unknown fields, carries both locales for every piece of
copy, and is frozen once built. The compiler translates the YAML the seven
forms use today into these models; nothing here reads YAML or Discord.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Locale = Literal["en-us", "pt-br"]
LOCALES: tuple[Locale, ...] = ("en-us", "pt-br")

ValueStyle = Literal[
    "channel",
    "role",
    "user",
    "bullet",
    "numbered",
    "code",
    "boolean",
    "boolean-mode",
    "mm_dd",
    "composition",
]
ButtonStyle = Literal["primary", "secondary", "success", "danger"]
Scalar = Union[str, bool, int]


class Node(BaseModel):
    """Base of every definition model: frozen, strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Text(Node):
    """One piece of copy in both locales."""

    en_us: str = Field(alias="en-us")
    pt_br: str = Field(alias="pt-br")

    def get(self, locale: str) -> str:
        """The copy for `locale`, English when the locale is not Portuguese."""
        return self.pt_br if locale == "pt-br" else self.en_us


class Count(Node):
    """Bounds on how many items a list holds."""

    min: int | None = None
    max: int | None = None


class Leaf(Node):
    """A single test on one key of the scope chain."""

    key: str
    is_: Scalar | None = Field(default=None, alias="is")
    in_: tuple[Scalar, ...] | None = Field(default=None, alias="in")
    not_in: tuple[Scalar, ...] | None = None
    matches: str | None = None
    present: bool | None = None
    absent: bool | None = None
    count: Count | None = None

    @model_validator(mode="after")
    def _one_operator(self) -> Leaf:
        operators = [
            self.is_,
            self.in_,
            self.not_in,
            self.matches,
            self.present,
            self.absent,
            self.count,
        ]
        if not any(operator is not None for operator in operators):
            raise ValueError(f"rule on {self.key!r} declares no operator")
        return self


class All(Node):
    """True when every rule holds."""

    all: tuple[When, ...]


class AnyOf(Node):
    """True when at least one rule holds."""

    any: tuple[When, ...]


class Not(Node):
    """True when the rule does not hold."""

    not_: When = Field(alias="not")


When = Union[Leaf, All, AnyOf, Not]


class DescriptionVariant(Node):
    """A description that replaces the default one while its rule holds."""

    when: When
    text: Text


class Option(Node):
    """One choice of a single-choice step or a card picker."""

    label: Text
    value: Scalar
    style: ButtonStyle | None = None


class Design(Node):
    """One entry of a design gallery."""

    key: str
    label: Text
    description: Text


class TextField(Node):
    """One input of a text step's modal."""

    key: str | None = None
    label: Text
    max_length: int | None = None
    required: bool = True
    default: Text | None = None
    placeholder: Text | None = None
    description: Text | None = None


class Select(Node):
    """One native select of a multi-pick step."""

    type: Literal["channels", "roles", "available_roles"]
    key: str
    style: ValueStyle | None = None
    icon: str | None = None
    label: Text
    placeholder: Text


class HeaderLine(Node):
    """A line under the card title, resolved from an earlier answer."""

    emoji: str = ""
    label: Text
    value_key: str
    value_format: ValueStyle | None = None


class Header(Node):
    """The card header: title, emoji, thumbnail and resolved lines."""

    title: Text
    title_emoji: str = ""
    thumbnail: str = ""
    lines: tuple[HeaderLine, ...] = ()


class CardField(Node):
    """A persisted key of a card and how summaries show it."""

    key: str
    label: Text
    description: Text | None = None
    style: ValueStyle | None = None
    hidden: bool = False


class TemplateVar(Node):
    """A named value substituted into card previews with `{name}`."""

    answer: str | None = None
    format: ValueStyle | None = None
    context: Literal["server_name"] | None = None

    @model_validator(mode="after")
    def _one_source(self) -> TemplateVar:
        if (self.answer is None) == (self.context is None):
            raise ValueError("a template var names an answer or a context value")
        return self


class SectionState(Node):
    """The state keys a card section owns."""

    value: str | None = None
    mode: str | None = None
    title: str | None = None
    content: str | None = None
    url: str | None = None

    def keys(self) -> tuple[str, ...]:
        """Every key the section writes, in declaration order."""
        return tuple(
            key
            for key in (self.value, self.mode, self.title, self.content, self.url)
            if key
        )


class ResetRule(Node):
    """Clear a dependent key when the named validator rejects the new pair."""

    key: str
    validation: str


class ModalSpec(Node):
    """The modal a section opens."""

    title: Text
    title_label: Text | None = None
    content_label: Text | None = None
    validation: str | None = None
    fields: tuple[TextField, ...] = ()


class SectionDefault(Node):
    """The default title and content of a title-content section."""

    title: Text
    content: Text


class SectionBase(Node):
    """What every card section declares."""

    key: str
    icon: str
    label: Text
    state: SectionState
    customize_label: Text | None = None
    visible_when: When | None = None
    style: ValueStyle | None = None
    picker_title: Text | None = None
    picker_description: Text | None = None


class TitleContentSection(SectionBase):
    """A title plus a body, default or custom."""

    type: Literal["title-content"]
    modal: ModalSpec
    default: SectionDefault


class FileUploadSection(SectionBase):
    """An image, default or uploaded."""

    type: Literal["file-upload"]
    modal: ModalSpec


class ChannelSelectSection(SectionBase):
    """One channel chosen on a native select."""

    type: Literal["channel-select"]


class ValueSelectSection(SectionBase):
    """One value chosen on a select of declared options."""

    type: Literal["value-select"]
    options: tuple[Option, ...]
    reset_on_change: tuple[ResetRule, ...] = ()


class ButtonOptionsSection(SectionBase):
    """One value chosen on a row of buttons."""

    type: Literal["button-options"]
    options: tuple[Option, ...]


class BooleanToggleSection(SectionBase):
    """A yes or no flipped by its button."""

    type: Literal["boolean-toggle"]


class ModalInputSection(SectionBase):
    """One text typed into a modal."""

    type: Literal["modal-input"]
    modal: ModalSpec


class MultiSelectSection(SectionBase):
    """Several values chosen on a select of declared options."""

    type: Literal["multi-select"]
    options: tuple[Option, ...]


Section = Annotated[
    Union[
        TitleContentSection,
        FileUploadSection,
        ChannelSelectSection,
        ValueSelectSection,
        ButtonOptionsSection,
        BooleanToggleSection,
        ModalInputSection,
        MultiSelectSection,
    ],
    Field(discriminator="type"),
]


class InfoField(Node):
    """One titled paragraph of an info screen."""

    title: str
    message: str


class InfoFields(Node):
    """The paragraphs of an info screen, per locale."""

    en_us: tuple[InfoField, ...] = Field(alias="en-us")
    pt_br: tuple[InfoField, ...] = Field(alias="pt-br")

    def get(self, locale: str) -> tuple[InfoField, ...]:
        """The paragraphs for `locale`."""
        return self.pt_br if locale == "pt-br" else self.en_us


class StepBase(Node):
    """What every step declares."""

    key: str
    title: Text
    description: Text
    footer: Text | None = None
    emoji: str | None = None
    hidden: bool = False
    required: bool = False
    when: When | None = None
    description_when: tuple[DescriptionVariant, ...] = ()
    style: ValueStyle | None = None


class IntroStep(StepBase):
    """The first screen: what the feature does, then Confirm."""

    kind: Literal["intro"]


class TextStep(StepBase):
    """A modal with one or more text inputs, or one file input."""

    kind: Literal["text"]
    input: Literal["text", "file"] = "text"
    label: Text | None = None
    placeholder: Text | None = None
    max_length: int = 40
    lowercase: bool = False
    multiline: bool = False
    enumerate: bool = False
    validation: str | None = None
    transform: str | None = None
    fields: tuple[TextField, ...] = ()
    modal_title: Text | None = None


class SingleChoiceStep(StepBase):
    """One or more buttons to pick from, or a design gallery."""

    kind: Literal["single_choice"]
    options: tuple[Option, ...] = ()
    designs: tuple[Design, ...] = ()
    unique: bool = False
    styled_values: bool = False
    auto_confirm: bool = False

    @model_validator(mode="after")
    def _options_or_designs(self) -> SingleChoiceStep:
        if bool(self.options) == bool(self.designs):
            raise ValueError(f"step {self.key!r} needs options or designs, not both")
        return self


class ChannelPickStep(StepBase):
    """A native channel select."""

    kind: Literal["channel_pick"]
    unique: bool = True


class RolePickStep(StepBase):
    """A native role select, optionally limited to roles the bot can assign."""

    kind: Literal["role_pick"]
    unique: bool = True
    available: bool = False


class UserPickStep(StepBase):
    """A native member select."""

    kind: Literal["user_pick"]
    unique: bool = True


class MultiPickStep(StepBase):
    """Several native selects on one screen."""

    kind: Literal["multi_pick"]
    selects: tuple[Select, ...]


class CardStep(StepBase):
    """A Components V2 card whose sections each edit part of the answer."""

    kind: Literal["card"]
    editable: bool = False
    required_keys: tuple[str, ...] = ()
    defaults: dict[str, Scalar | Text] = Field(default_factory=dict)
    header: Header | None = None
    fields: tuple[CardField, ...] = ()
    sections: tuple[Section, ...]
    transform: str | None = None
    context: dict[str, TemplateVar] = Field(default_factory=dict)

    def state_keys(self) -> tuple[str, ...]:
        """Every state key the sections own, in declaration order."""
        keys: list[str] = []
        for section in self.sections:
            for key in section.state.keys():
                if key not in keys:
                    keys.append(key)
        return tuple(keys)


class ItemsSpec(Node):
    """How many items a composition holds and what makes one unique."""

    max: int
    unique_by: str | None = None


class InfoStep(StepBase):
    """A read-only screen with titled paragraphs and Confirm."""

    kind: Literal["info"]
    fields: InfoFields | None = None


class ReviewStep(StepBase):
    """The last screen: the summary and the final Confirm."""

    kind: Literal["review"]
    preview: bool = False


class CompositionStep(StepBase):
    """A list of items, each built by a child session over `steps`."""

    kind: Literal["composition"]
    parent_key: str
    items: ItemsSpec
    steps: tuple[Step, ...]


Step = Annotated[
    Union[
        IntroStep,
        TextStep,
        SingleChoiceStep,
        ChannelPickStep,
        RolePickStep,
        UserPickStep,
        MultiPickStep,
        CardStep,
        CompositionStep,
        InfoStep,
        ReviewStep,
    ],
    Field(discriminator="kind"),
]

STEP_KINDS: tuple[str, ...] = (
    "intro",
    "text",
    "single_choice",
    "channel_pick",
    "role_pick",
    "user_pick",
    "multi_pick",
    "card",
    "composition",
    "info",
    "review",
)


class FormDefinition(Node):
    """A compiled, versioned form: the only thing the engine reads at runtime."""

    key: str
    version: int = 1
    steps: tuple[Step, ...]

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
    def composition(self) -> CompositionStep | None:
        """The composition step, when the form has one."""
        for step in self.steps:
            if isinstance(step, CompositionStep):
                return step
        return None

    @property
    def items(self) -> ItemsSpec | None:
        """The item limits of the composition, when the form has one."""
        composition = self.composition
        return composition.items if composition else None


for _model in (All, AnyOf, Not, DescriptionVariant, SectionBase, CompositionStep):
    _model.model_rebuild()
FormDefinition.model_rebuild()

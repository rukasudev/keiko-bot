"""What a form YAML may declare, how it is read and how it is compiled."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.constants import DiscordLimits, ViewConstants
from app.settings.form.responses.transforms import NORMALIZERS, TRANSFORMS
from app.settings.form.responses.validations import VALIDATORS


def same_value(left: Any, right: Any) -> bool:
    """Equality across the spellings a value takes in YAML and in answers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return str(left).lower() == str(right).lower()
    return str(left) == str(right)


class DefinitionSource(Protocol):
    """A place raw form definitions are read from."""

    def load(self, key: str) -> Mapping[str, Any]:
        """The raw definition of the form `key`."""

    def list(self) -> tuple[str, ...]:
        """Every form key the source knows."""


class DiskSource:
    """The YAML files under `app/languages/form/`."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("app") / "languages" / "form"

    def load(self, key: str) -> Mapping[str, Any]:
        """The parsed YAML of `<root>/<key>.yml`."""
        path = self.root / f"{key.lower()}.yml"
        with path.open(encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, Mapping):
            raise ValueError(f"{path} is not a mapping")
        return loaded

    def list(self) -> tuple[str, ...]:
        """Every form key with a file under the root."""
        return tuple(sorted(path.stem for path in self.root.glob("*.yml")))


Locale = Literal["en-us", "pt-br"]
LOCALES: tuple[Locale, ...] = ("en-us", "pt-br")

ValueStyle = Literal[
    "channel",
    "role",
    "user",
    "bullet",
    "code-block",
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


class PanelGroupRef(Node):
    """A heading several settings share on the panel and the review."""

    key: str
    title: Text | None = None
    title_when: tuple[DescriptionVariant, ...] = ()
    description: Text | None = None
    description_when: tuple[DescriptionVariant, ...] = ()
    emoji: str | None = None
    own_screen: bool = False
    entry: Literal["inline", "footer"] = "inline"
    label: Text | None = None
    order: int = 0


class Option(Node):
    """One choice of a single-choice step or a card picker."""

    label: Text
    value: Scalar
    style: ButtonStyle | None = None
    description: Text | None = None


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
    normalize: str | None = None
    multiline: bool = False


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
    icon: str | None = None
    description: Text | None = None
    style: ValueStyle | None = None
    hidden: bool = False
    edit_label: Text | None = None
    panel_group: PanelGroupRef | None = None


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


class Fills(Node):
    """Copy a typed value writes into another key of its card."""

    into: str
    tokens: dict[str, str]


class SectionBase(Node):
    """What every card section declares."""

    key: str
    icon: str
    label: Text
    state: SectionState = Field(default_factory=SectionState)
    customize_label: Text | None = None
    description: Text | None = None
    media_key: str = ""
    visible_when: When | None = None
    setup_only: bool = False
    style: ValueStyle | None = None
    picker_title: Text | None = None
    picker_description: Text | None = None

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

    def validators(self) -> tuple[str, ...]:
        """Every validator the section names."""
        modal = self.opened_modal()
        return (modal.validation,) if modal and modal.validation else ()

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
        return 1 + (2 if self.state.mode else 1)

    def pictured(self) -> bool:
        """True when the section can draw a picture under its value."""
        return bool(self.media_key or self.state.url or self.offered_designs())

    def picks_members(self) -> bool:
        """True when the section's value is a member picked on a member select."""
        return False

    def holds(self) -> tuple[str, ...]:
        """The keys of the card's sections it draws on its own screen, none mostly."""
        return ()


class TitleContentSection(SectionBase):
    """A title plus a body, default or custom."""

    type: Literal["title-content"]
    modal: ModalSpec
    default: SectionDefault

    def opened_modal(self) -> ModalSpec:
        """The modal that types the title and the body."""
        return self.modal


class FileUploadSection(SectionBase):
    """An image, default or uploaded."""

    type: Literal["file-upload"]
    modal: ModalSpec

    def opened_modal(self) -> ModalSpec:
        """The modal that uploads the image."""
        return self.modal


class ChannelSelectSection(SectionBase):
    """One channel chosen on a native select."""

    type: Literal["channel-select"]

    def controls_drawn(self) -> int:
        """The channel select and its row."""
        return 2


class UserSelectSection(SectionBase):
    """One member chosen on a native member select, the member's picture under it."""

    type: Literal["user-select"]
    picture: bool = False

    def controls_drawn(self) -> int:
        """The member select and its row."""
        return 2

    def pictured(self) -> bool:
        """True when the section draws the chosen member's picture."""
        return self.picture or bool(self.media_key)

    def picks_members(self) -> bool:
        """The value is a member."""
        return True


class ValueSelectSection(SectionBase):
    """One value chosen on a select of declared options."""

    type: Literal["value-select"]
    options: tuple[Option, ...]
    reset_on_change: tuple[ResetRule, ...] = ()

    def choices(self) -> tuple[Option, ...]:
        """The declared options."""
        return self.options

    def validators(self) -> tuple[str, ...]:
        """The validators that decide which dependent keys a change clears."""
        return tuple(rule.validation for rule in self.reset_on_change)

    def controls_drawn(self) -> int:
        """The options select and its row."""
        return 2


class ButtonOptionsSection(SectionBase):
    """One value chosen on a row of buttons."""

    type: Literal["button-options"]
    options: tuple[Option, ...]

    def choices(self) -> tuple[Option, ...]:
        """The declared options."""
        return self.options

    def controls_drawn(self) -> int:
        """The options select and its row."""
        return 2


class BooleanToggleSection(SectionBase):
    """A yes or no chosen on two buttons."""

    type: Literal["boolean-toggle"]

    def controls_drawn(self) -> int:
        """The yes and no buttons and their row."""
        return 3


class ModalInputSection(SectionBase):
    """One text typed into a modal."""

    type: Literal["modal-input"]
    modal: ModalSpec
    lookup_answers: dict[str, str] = Field(default_factory=dict)
    fills: Fills | None = None

    def opened_modal(self) -> ModalSpec:
        """The modal its fields are typed in."""
        return self.modal

    def looked_up(self) -> Mapping[str, str]:
        """The lookup values kept as answers, by answer key."""
        return self.lookup_answers

    def filled(self) -> Fills | None:
        """The copy the typed value writes into another key of the card."""
        return self.fills


class MultiSelectSection(SectionBase):
    """Several values chosen on a select of declared options."""

    type: Literal["multi-select"]
    options: tuple[Option, ...]

    def choices(self) -> tuple[Option, ...]:
        """The declared options."""
        return self.options

    def holds_several(self) -> bool:
        """A multi-select keeps several options."""
        return True

    def controls_drawn(self) -> int:
        """The options select and its row."""
        return 2


class SelectsSection(SectionBase):
    """Channels or roles picked on native selects, every select on one screen."""

    type: Literal["selects"]
    selects: tuple[Select, ...] = Field(min_length=1)

    def picks(self) -> tuple[Select, ...]:
        """The declared selects, each writing its own key."""
        return self.selects

    def controls_drawn(self) -> int:
        """Each select and its row."""
        return 2 * len(self.selects)


class ListSection(SectionBase):
    """A list of the form drawn on the card: its items, Add and Remove."""

    type: Literal["list"]
    list: str

    def adds_to(self) -> str:
        """The composition the section's Add opens an item of."""
        return self.list

    def controls_drawn(self) -> int:
        """Add and Remove and their row."""
        return 3


class GroupSection(SectionBase):
    """Other sections of the card, drawn on a screen of their own behind a button."""

    type: Literal["group"]
    sections: tuple[str, ...] = Field(min_length=1)

    def holds(self) -> tuple[str, ...]:
        """The keys of the sections its screen draws."""
        return self.sections


class DesignSection(SectionBase):
    """One design chosen on a gallery of previews."""

    type: Literal["design-select"]
    designs: tuple[Design, ...]
    draws_from: str = ""

    def choices(self) -> tuple[Option, ...]:
        """The designs as options, labelled by their names."""
        return tuple(
            Option(label=design.label, value=design.key, description=design.description)
            for design in self.designs
        )

    def controls_drawn(self) -> int:
        """The designs select and its row."""
        return 2

    def offered_designs(self) -> tuple[Design, ...]:
        """The declared designs."""
        return self.designs

    def drawn_over(self) -> str:
        """The answer the previews are drawn over."""
        return self.draws_from


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

    key: str
    title: Text
    description: Text
    footer: Text | None = None
    emoji: str | None = None
    hidden: bool = False
    required: bool = False
    in_setup: bool = True
    when: When | None = None
    description_when: tuple[DescriptionVariant, ...] = ()
    title_when: tuple[DescriptionVariant, ...] = ()
    style: ValueStyle | None = None
    edit_label: Text | None = None
    panel_group: PanelGroupRef | None = None


class IntroStep(StepBase):
    """The first screen: what the feature does, then Confirm."""

    kind: Literal["intro"]
    manager_description: Text | None = None


class TextStep(StepBase):
    """A modal with one or more text inputs, or one file input."""

    kind: Literal["text"]
    input: Literal["text", "file"] = "text"
    label: Text | None = None
    placeholder: Text | None = None
    max_length: int = 40
    lowercase: bool = False
    normalize: str | None = None
    multiline: bool = False
    enumerate: bool = False
    validation: str | None = None
    transform: str | None = None
    fields: tuple[TextField, ...] = ()
    lookup_answers: dict[str, str] = Field(default_factory=dict)
    media_key: str = ""
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
    edit_by_field: bool = False


def owned_keys(section: Section) -> tuple[str, ...]:
    """The state keys a section writes, its modal's keyed fields and its selects."""
    keys = list(section.state.keys())
    keys += [select.key for select in section.picks() if select.key not in keys]
    modal = section.opened_modal()
    if modal is not None:
        keys += [
            field.key for field in modal.fields if field.key and field.key not in keys
        ]
    return tuple(keys)


class PreviewNeeds(Node):
    """The keys a card's preview is drawn from, and the error while one is empty."""

    keys: tuple[str, ...]
    error: str


class CardStep(StepBase):
    """A Components V2 card whose sections each edit part of the answer."""

    kind: Literal["card"]
    editable: bool = False
    edit_by_field: bool = False
    preview: bool = False
    preview_needs: PreviewNeeds | None = None
    required_keys: tuple[str, ...] = ()
    required_any: tuple[str, ...] = ()
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
            for key in owned_keys(section):
                if key not in keys:
                    keys.append(key)
        return tuple(keys)


class ItemsSpec(Node):
    """How many items a composition holds, what tells them apart, what each may do."""

    max: int
    unique_by: str | None = None
    remove_each: bool = False
    unique_locked: bool = False


class ReviewStep(StepBase):
    """The last screen: the summary and the final Confirm."""

    kind: Literal["review"]
    preview: bool = False
    lines: tuple[HeaderLine, ...] = ()
    info: Text | None = None


class CompositionStep(StepBase):
    """A list of items, each built by a child session over `steps`."""

    kind: Literal["composition"]
    edit_by_item: bool = False
    search: bool = False
    parent_key: str
    items: ItemsSpec
    title_count: Text | None = None
    only_item_warning: Text | None = None
    found_title: Text | None = None
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
    "review",
)


def listed_on_cards(steps: Sequence[Step]) -> frozenset[str]:
    """The lists a card among `steps` draws and adds to, which a setup never walks."""
    return frozenset(
        section.adds_to()
        for step in steps
        if isinstance(step, CardStep)
        for section in step.sections
        if section.adds_to()
    )


class FormDefinition(Node):
    """A compiled, versioned form: the only thing the engine reads at runtime."""

    key: str
    version: int = 1
    steps: tuple[Step, ...]

    def draws_from(self) -> str:
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
            if isinstance(step, SingleChoiceStep):
                found += step.designs
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


KIND_BY_ACTION: dict[str, str] = {
    "form": "intro",
    "modal": "text",
    "file_upload": "text",
    "options": "single_choice",
    "design_select": "single_choice",
    "channels": "channel_pick",
    "roles": "role_pick",
    "available_roles": "role_pick",
    "user_select": "user_pick",
    "multi_select": "multi_pick",
    "configuration_card": "card",
    "composition": "composition",
    "resume": "review",
}

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


@dataclass(frozen=True)
class Compiled:
    """A compiled definition and the deprecation warnings raised on the way."""

    definition: FormDefinition
    warnings: tuple[str, ...]


def compile_form(key: str, raw: Mapping[str, Any]) -> FormDefinition:
    """The definition of the form `key`, or a `CompileError`."""
    return compile_with_warnings(key, raw).definition


def compile_with_warnings(key: str, raw: Mapping[str, Any]) -> Compiled:
    """Translate, validate and check `raw`; keep the warnings alongside."""
    warnings: list[str] = []
    steps = raw.get("steps")

    if not isinstance(steps, list) or not steps:
        raise CompileError(key, None, "steps", "a form declares a non-empty steps list")
    translated = [_translate_step(key, step, warnings) for step in steps]
    _qualify_scopes(key, translated, outer=(), warnings=warnings)
    payload = {"key": key, "version": int(raw.get("version", 1)), "steps": translated}

    try:
        definition = FormDefinition.model_validate(payload)
    except ValidationError as error:
        raise _as_compile_error(key, error) from None
    _check_shape(definition)
    _check_scope(definition, definition.steps, outer=())
    _check_registries(definition)
    _check_limits(definition)

    return Compiled(definition, tuple(warnings))


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


def _legacy_condition(
    form: str, condition: Mapping[str, Any], warnings: list[str]
) -> Any:
    """`condition: {key, not_in, matches}` as a `when` rule."""
    key = condition.get("key")
    leaves: list[dict[str, Any]] = []

    if "not_in" in condition:
        leaves.append({"key": key, "not_in": list(condition["not_in"])})
    if "matches" in condition:
        leaves.append({"key": key, "matches": condition["matches"]})
    unknown = set(condition) - {"key", "not_in", "matches"}
    if unknown or key is None or not leaves:
        raise CompileError(
            form, None, "condition", f"unsupported condition {condition}"
        )
    warnings.append(f"{form}: `condition` on {key!r} is deprecated, use `when`")
    return leaves[0] if len(leaves) == 1 else {"all": leaves}


def _rule(form: str, raw: Mapping[str, Any], field: str, warnings: list[str]) -> Any:
    if "when" in raw:
        return raw["when"]
    if field in raw:
        return _legacy_condition(form, raw[field], warnings)
    return None


def _translate_common(
    form: str, raw: Mapping[str, Any], warnings: list[str]
) -> dict[str, Any]:
    out = {
        key: value
        for key, value in raw.items()
        if key not in ("action", "condition", "when")
    }
    rule = _rule(form, raw, "condition", warnings)

    if rule is not None:
        out["when"] = rule
    for spelled, field in (
        ("description-when", "description_when"),
        ("title-when", "title_when"),
    ):
        variants = out.pop(spelled, None)
        if variants:
            out[field] = [
                {
                    "when": _rule(form, variant, "condition", warnings),
                    "text": {
                        key: value
                        for key, value in variant.items()
                        if key in ("en-us", "pt-br")
                    },
                }
                for variant in variants
            ]
    if "response_transform" in out:
        out["transform"] = out.pop("response_transform")
    return out


def _translate_step(
    form: str, raw: Mapping[str, Any], warnings: list[str]
) -> dict[str, Any]:
    action = raw.get("action")
    kind = KIND_BY_ACTION.get(str(action))

    if kind is None:
        raise CompileError(form, raw.get("key"), "action", f"unknown action {action!r}")
    out = _translate_common(form, raw, warnings)
    out["kind"] = kind

    if action == "file_upload":
        out["input"] = "file"
    if action == "available_roles":
        out["available"] = True
    if action in ("channels", "roles", "available_roles"):
        _translate_pick(form, raw, out)
    if action == "resume" and "lines" in out:
        out["lines"] = _translate_lines(out.get("lines"))
    if action == "configuration_card":
        _translate_card(form, out)
    if action == "composition":
        _translate_composition(form, out, warnings)
    return out


def _translate_pick(form: str, raw: Mapping[str, Any], out: dict[str, Any]) -> None:
    if not raw.get("select", False):
        raise CompileError(
            form, raw.get("key"), "select", "only native selects are supported"
        )
    out.pop("select", None)


def _translate_card(form: str, out: dict[str, Any]) -> None:
    if "required" in out:
        out["required_keys"] = list(out.pop("required") or [])
    if "template-vars" in out:
        out["context"] = {
            name: _translate_template_var(form, out.get("key"), spec)
            for name, spec in (out.pop("template-vars") or {}).items()
        }
    header = out.get("header")
    if isinstance(header, Mapping):
        out["header"] = _translate_header(header)
    out["sections"] = [
        _translate_section(section) for section in out.get("sections", [])
    ]


def _translate_template_var(
    form: str, step_key: Any, spec: Mapping[str, Any]
) -> dict[str, Any]:
    source = spec.get("from", "response")
    if source == "response":
        return {"answer": spec.get("key"), "format": spec.get("format")}
    if source == "interaction" and spec.get("attr") == "guild.name":
        return {"context": "server_name"}
    raise CompileError(form, step_key, "template-vars", f"unsupported source {spec}")


def _translate_lines(lines: Any) -> list[dict[str, Any]]:
    return [
        {
            "emoji": line.get("emoji", ""),
            "label": line.get("label"),
            "value_key": line.get("value-key"),
            "value_format": line.get("value-format"),
        }
        for line in lines or []
    ]


def _translate_header(header: Mapping[str, Any]) -> dict[str, Any]:
    out = {
        key.replace("-", "_"): value for key, value in header.items() if key != "lines"
    }
    out["lines"] = _translate_lines(header.get("lines"))
    return out


def _translate_section(section: Mapping[str, Any]) -> dict[str, Any]:
    out = {key.replace("-", "_"): value for key, value in section.items()}
    modal = out.get("modal")
    if isinstance(modal, Mapping):
        out["modal"] = {key.replace("-", "_"): value for key, value in modal.items()}
    return out


def _translate_composition(form: str, out: dict[str, Any], warnings: list[str]) -> None:
    if "max" not in out:
        raise CompileError(form, out.get("key"), "max", "a composition declares max")
    out["items"] = {
        "max": out.pop("max"),
        "unique_by": out.pop("unique_by", None),
        "remove_each": out.pop("remove_each", False),
        "unique_locked": out.pop("unique_locked", False),
    }
    out["steps"] = [
        _translate_step(form, step, warnings) for step in out.get("steps", [])
    ]


def _raw_produced_keys(step: Mapping[str, Any]) -> list[str]:
    keys = [str(step.get("key"))]
    keys += [str(select.get("key")) for select in step.get("selects", []) or []]
    for section in step.get("sections", []) or []:
        keys += [str(value) for value in (section.get("state") or {}).values() if value]
        keys += [str(select.get("key")) for select in section.get("selects", []) or []]
        modal = section.get("modal") or {}
        keys += [
            str(field.get("key"))
            for field in modal.get("fields", []) or []
            if field.get("key")
        ]
    if step.get("kind") == "card":
        keys += [str(field.get("key")) for field in step.get("fields", []) or []]
    keys += [str(key) for key in (step.get("lookup_answers") or {})]
    return keys


def _raw_leaves(rule: Any) -> list[dict[str, Any]]:
    if not isinstance(rule, Mapping):
        return []
    if "key" in rule:
        return [rule] if isinstance(rule, dict) else [dict(rule)]
    for combinator in ("all", "any"):
        if combinator in rule:
            return [leaf for child in rule[combinator] for leaf in _raw_leaves(child)]
    return _raw_leaves(rule.get("not"))


def _qualify_leaf(
    form: str,
    step_key: Any,
    leaf: dict[str, Any],
    scopes: Sequence[Sequence[str]],
    warnings: list[str],
) -> None:
    """A bare key only produced by an outer session becomes `parent.key`."""
    key = str(leaf.get("key"))
    if key.startswith(("parent.", "item.")) or key == "items.count":
        return
    if key in scopes[-1]:
        return
    for depth, produced in enumerate(reversed(scopes[:-1]), start=1):
        if key in produced:
            leaf["key"] = "parent." * depth + key
            warnings.append(
                f"{form}.{step_key}: {key!r} resolved through the parent session,"
                f" spell it {leaf['key']!r}"
            )
            return


def _qualify_scopes(
    form: str,
    steps: Sequence[dict[str, Any]],
    outer: Sequence[Sequence[str]],
    warnings: list[str],
) -> None:
    produced: list[str] = []
    for step in steps:
        scopes = [*outer, produced]
        groups = [step.get("panel_group")] + [
            field.get("panel_group") for field in step.get("fields", []) or []
        ]
        rules = [step.get("when")] + [
            variant.get("when") for variant in step.get("description_when", [])
        ]
        titles = [
            variant.get("when")
            for ref in groups
            if isinstance(ref, Mapping)
            for variant in ref.get("title_when", []) or []
        ] + [variant.get("when") for variant in step.get("title_when", []) or []]

        for rule in rules:
            for leaf in _raw_leaves(rule):
                _qualify_leaf(form, step.get("key"), leaf, scopes, warnings)
        saved = [*outer, produced + _raw_produced_keys(step)]

        for rule in titles:
            for leaf in _raw_leaves(rule):
                _qualify_leaf(form, step.get("key"), leaf, saved, warnings)
        if step.get("kind") == "composition":
            _qualify_scopes(form, step.get("steps", []), [*outer, produced], warnings)
        produced.extend(_raw_produced_keys(step))


def _check_shape(definition: FormDefinition) -> None:
    steps = definition.steps
    if steps[0].kind != "intro":
        raise CompileError(
            definition.key, steps[0].key, "kind", "the first step is intro"
        )
    if steps[-1].kind != "review":
        raise CompileError(
            definition.key, steps[-1].key, "kind", "the last step is review"
        )
    seen: set[str] = set()
    for step in steps:
        if step.key in seen:
            raise CompileError(definition.key, step.key, "key", "duplicated step key")
        seen.add(step.key)


def produced_keys(step: Step) -> tuple[str, ...]:
    """The answer keys a step writes: its own plus its selects and card keys."""
    keys = [step.key]
    if isinstance(step, TextStep):
        keys = [field.key for field in step.fields if field.key] + keys
        keys += list(step.lookup_answers)
    if isinstance(step, MultiPickStep):
        keys += [select.key for select in step.selects]
    if isinstance(step, CardStep):
        keys += [key for key in step.state_keys() if key not in keys]
        keys += [field.key for field in step.fields if field.key not in keys]
    return tuple(keys)


def options_for(steps: Sequence[Step], key: str) -> tuple[Option, ...] | None:
    """The declared options behind `key`, when the producing step has them."""
    for step in steps:
        if isinstance(step, SingleChoiceStep) and step.key == key and step.options:
            return step.options
        if isinstance(step, CardStep):
            for section in step.sections:
                choices = section.choices()
                if choices and section.state.value == key:
                    return choices
    return None


def _leaves(rule: When | None) -> list[Leaf]:
    if rule is None:
        return []
    if isinstance(rule, Leaf):
        return [rule]
    if isinstance(rule, All):
        return [leaf for child in rule.all for leaf in _leaves(child)]
    if isinstance(rule, AnyOf):
        return [leaf for child in rule.any for leaf in _leaves(child)]
    return _leaves(rule.not_)


def _check_leaf(
    form: str,
    step_key: str,
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


def _panel_group_refs(step: Step) -> list[PanelGroupRef]:
    """Every panel group a step or one of its card fields declares."""
    found = [step.panel_group] if step.panel_group else []
    if isinstance(step, CardStep):
        found += [field.panel_group for field in step.fields if field.panel_group]
    return found


def _check_scope(
    definition: FormDefinition,
    steps: Sequence[Step],
    outer: Sequence[tuple[tuple[str, ...], Sequence[Step]]],
) -> None:
    produced: list[str] = []
    for step in steps:
        scopes = list(outer) + [(tuple(produced), steps)]
        for leaf in _leaves(step.when):
            _check_leaf(definition.key, step.key, leaf, scopes)
        for variant in step.description_when:
            for leaf in _leaves(variant.when):
                _check_leaf(definition.key, step.key, leaf, scopes)
        saved = list(outer) + [(tuple(produced) + produced_keys(step), steps)]
        titles = [ref.title_when for ref in _panel_group_refs(step)]
        for variants in [step.title_when, *titles]:
            for variant in variants:
                for leaf in _leaves(variant.when):
                    _check_leaf(definition.key, step.key, leaf, saved)
        if isinstance(step, CardStep):
            _check_card(definition, step)
        if isinstance(step, CompositionStep):
            _check_scope(
                definition, step.steps, list(outer) + [(tuple(produced), steps)]
            )
        produced.extend(produced_keys(step))


def _check_modal(
    definition: FormDefinition, card: CardStep, section: Section, modal: ModalSpec
) -> None:
    fields = modal.fields
    if len(fields) > DiscordLimits.MODAL_INPUTS:
        raise CompileError(
            definition.key,
            card.key,
            "modal",
            f"a modal holds at most {DiscordLimits.MODAL_INPUTS} inputs",
        )
    if any(field.key is None for field in fields) and not section.state.value:
        raise CompileError(
            definition.key,
            card.key,
            "state",
            "fields without a key need state.value",
        )
    keyed = [field.key for field in fields if field.key]

    if fields and len(keyed) == len(fields) and section.state.value not in keyed:
        raise CompileError(
            definition.key,
            card.key,
            "state",
            "state.value names none of the fields",
        )


def _check_lists(definition: FormDefinition, card: CardStep) -> None:
    lists = {step.key for step in definition.steps if isinstance(step, CompositionStep)}
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
        modal = section.opened_modal()
        if modal is not None:
            _check_modal(definition, card, section, modal)
    for section in card.sections:
        for leaf in _leaves(section.visible_when):
            if leaf.key not in state_keys:
                raise CompileError(
                    definition.key, card.key, "visible-when", f"{leaf.key!r} unknown"
                )
    _check_groups(definition, card)
    known = state_keys | {field.key for field in card.fields}
    needs = card.preview_needs
    if needs is not None and not card.preview:
        raise CompileError(
            definition.key, card.key, "preview_needs", "the card draws no preview"
        )
    for name, keys in (
        ("required", card.required_keys),
        ("required_any", card.required_any),
        ("preview_needs", needs.keys if needs is not None else ()),
    ):
        for key in keys:
            if key not in known:
                raise CompileError(definition.key, card.key, name, f"{key!r} unknown")
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
        if section.media_key and section.media_key not in state_keys | looked_up:
            raise CompileError(
                definition.key,
                card.key,
                "media_key",
                f"{section.media_key!r} is no state key or lookup answer of the card",
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
        if isinstance(step, CompositionStep):
            found.extend(_walk(step.steps))
    return found


def _check_edit_by_field(definition: FormDefinition) -> None:
    for outer in definition.steps:
        if not isinstance(outer, CompositionStep):
            continue
        for inner in outer.steps:
            if isinstance(inner, (CardStep, MultiPickStep)) and inner.edit_by_field:
                raise CompileError(
                    definition.key,
                    inner.key,
                    "edit_by_field",
                    "edit_by_field is not allowed inside a composition",
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
        if step.media_key and step.media_key not in step.lookup_answers:
            raise CompileError(
                definition.key,
                step.key,
                "media_key",
                f"{step.media_key!r} is no lookup answer of the step",
            )


def _check_item_keys(definition: FormDefinition) -> None:
    for step in _walk(definition.steps):
        if not isinstance(step, CompositionStep):
            continue
        produced = {key for inner in step.steps for key in produced_keys(inner)}
        unique = step.items.unique_by or ""
        if unique and unique not in produced:
            raise CompileError(
                definition.key, step.key, "unique_by", f"{unique!r} is no item key"
            )
        if step.search and not picks_a_member(step.steps, unique):
            raise CompileError(
                definition.key,
                step.key,
                "search",
                "search finds an item by its member: unique_by is picked on a "
                "member select",
            )
        if step.items.remove_each and not step.edit_by_item:
            raise CompileError(
                definition.key,
                step.key,
                "remove_each",
                "a button beside each item needs edit_by_item",
            )
        if step.items.remove_each and step.required and not step.only_item_warning:
            raise CompileError(
                definition.key,
                step.key,
                "only_item_warning",
                "removing the only item of a required list turns it off: say so",
            )


def _check_entries(definition: FormDefinition) -> None:
    for step in definition.steps:
        for ref in _panel_group_refs(step):
            if ref.entry == "footer" and not (ref.own_screen and ref.label):
                raise CompileError(
                    definition.key,
                    step.key,
                    "panel_group.entry",
                    "a footer entry opens a screen of its own and names its button",
                )


def _check_registries(definition: FormDefinition) -> None:
    _check_edit_by_field(definition)
    _check_lookup_answers(definition)
    _check_item_keys(definition)
    _check_entries(definition)
    for step in _walk(definition.steps):
        names = list(_card_validations(step))
        if isinstance(step, TextStep) and step.validation:
            names.append(step.validation)
        for name in names:
            if name not in VALIDATORS:
                raise CompileError(
                    definition.key,
                    step.key,
                    "validation",
                    f"unknown validator {name!r}",
                )
        transform = step.transform if isinstance(step, (TextStep, CardStep)) else None
        if transform and transform not in TRANSFORMS:
            raise CompileError(
                definition.key,
                step.key,
                "transform",
                f"unknown transform {transform!r}",
            )
        for normalize in _normalizers(step):
            if normalize not in NORMALIZERS:
                raise CompileError(
                    definition.key,
                    step.key,
                    "normalize",
                    f"unknown normalizer {normalize!r}",
                )


def _normalizers(step: Step) -> list[str]:
    fields: list[TextField] = []
    names: list[str] = []
    if isinstance(step, TextStep):
        fields += list(step.fields)
        if step.normalize:
            names.append(step.normalize)
    if isinstance(step, CardStep):
        for section in step.sections:
            modal = section.opened_modal()
            fields += list(modal.fields) if modal is not None else []
    return names + [field.normalize for field in fields if field.normalize]


def _card_validations(step: Step) -> list[str]:
    if not isinstance(step, CardStep):
        return []
    names: list[str] = []
    for section in step.sections:
        names += section.validators()
    return names


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
    header = 4 + (len(card.header.lines) if card.header else 0)
    frame = 1 + header + 1 + 1
    held = {key for group in card.sections for key in group.holds()}
    sections = 0

    for section in card.sections:
        if section.key in held:
            continue
        drawn = _section_components(section)
        if section.holds():
            drawn = max(drawn, _held_components(card, section))
        sections += drawn
    buttons = 2 + int(back) + int(card.preview)
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
            yield step, any(before.kind != "intro" for before in steps[:index])
        if isinstance(step, CompositionStep):
            yield from cards_with_back(step.steps)


def picks_a_member(steps: Sequence[Step], key: str) -> bool:
    """True when the step that produces `key` picks it on a member select."""
    for step in steps:
        if isinstance(step, UserPickStep) and step.key == key:
            return True
        if isinstance(step, CardStep) and any(
            section.picks_members() and key in owned_keys(section)
            for section in step.sections
        ):
            return True
    return False


def _check_limits(definition: FormDefinition) -> None:
    for step in _walk(definition.steps):
        limit = ViewConstants.EDIT_BY_ITEM_MAX
        if isinstance(step, CompositionStep) and step.edit_by_item:
            if step.items.max > limit:
                raise CompileError(
                    definition.key,
                    step.key,
                    "edit_by_item",
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
        self.warnings: tuple[str, ...] = ()

    def load_all(self) -> tuple[FormDefinition, ...]:
        """Compile every form the source lists; a bad one raises `CompileError`."""
        warnings: list[str] = []
        for key in self.source.list():
            compiled = compile_with_warnings(key, self.source.load(key))
            self._register(compiled.definition)
            warnings.extend(compiled.warnings)
        self.warnings = tuple(warnings)
        return tuple(self._definitions.values())

    def _register(self, definition: FormDefinition) -> None:
        self._definitions[(definition.key, definition.version)] = definition
        self._latest[definition.key] = max(
            definition.version, self._latest.get(definition.key, 0)
        )

    def get(self, key: str, version: int | None = None) -> FormDefinition:
        """The definition of `key` at `version`, the latest when not given."""
        if key not in self._latest:
            self._register(compile_with_warnings(key, self.source.load(key)).definition)
        return self._definitions[(key, version or self._latest[key])]

    def keys(self) -> tuple[str, ...]:
        """Every form key loaded so far."""
        return tuple(sorted(self._latest))


registry = DefinitionRegistry()

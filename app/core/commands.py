"""Every command Keiko has, declared once, and the registry that reads them and the
files beside them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field
from yaml.events import NodeEvent

if TYPE_CHECKING:
    from pydantic import BaseModel

    from app.forms.features.feature import FeatureModule


class JoinStage(Enum):
    """When a member hook runs: the member is changed before the join is announced."""

    CHANGES_MEMBER = 0
    ANNOUNCES = 1


@dataclass(frozen=True)
class MemberJoined:
    """A member who joined a server."""

    guild_id: str
    user_id: str
    member: Any


@dataclass(frozen=True)
class MessageSent:
    """A message someone sent, its server's prefix once known, and whether the prefix
    chain ended on it."""

    guild_id: str | None
    user_id: str
    channel_id: str
    content: str
    message: Any
    prefix: str = ""
    prefix_handled: bool = False


@dataclass(frozen=True)
class MessageEdited:
    """A message edited after it was sent, as Discord reports the edit."""

    guild_id: str | None
    channel_id: str
    payload: Any
    client: Any


@dataclass(frozen=True)
class GuildJoined:
    """A server that added Keiko."""

    guild_id: str
    guild: Any


@dataclass(frozen=True)
class Reminder:
    """A reminder the reminders service fired back."""

    id: str
    title: str
    notes: str | None = None


@dataclass(frozen=True)
class WebhookEvent:
    """An event an outside service posted, parsed by its integration."""

    name: str
    subject: str
    payload: Any = None


@dataclass(frozen=True)
class WebhookReply:
    """What the integration answers the service that posted an event."""

    status: int
    body: str


MemberHook = Callable[[MemberJoined], Awaitable[None]]
MessageHook = Callable[[MessageSent], Awaitable[None]]
PrefixHook = Callable[[MessageSent], Awaitable[bool]]
EditHook = Callable[[MessageEdited], Awaitable[None]]
GuildJoinHook = Callable[[GuildJoined], Awaitable[None]]
ViewsHook = Callable[[], Iterable[Any]]
ReminderHandler = Callable[[Reminder], None]
WebhookHandler = Callable[[WebhookEvent], WebhookReply]
Opener = Callable[[Any, str], Awaitable[None]]


_Child = TypeVar("_Child")

Open = dict[str, _Child]
"""A YAML mapping whose keys the author chooses, each child of the given type."""


class Text(BaseModel):
    """One text in both locales Keiko speaks, the node every form and copy file
    writes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    en_us: str = Field(
        alias="en-us",
        description="What Keiko writes to an English-speaking server or member.",
        examples=["All set!"],
        json_schema_extra={"role": "copy"},
    )
    pt_br: str = Field(
        alias="pt-br",
        description="What Keiko writes to a Portuguese-speaking server or member.",
        examples=["Prontinho!"],
        json_schema_extra={"role": "copy"},
    )

    def get(self, locale: str) -> str:
        """The text for `locale`, English when the locale is not Portuguese."""
        return self.pt_br if locale == "pt-br" else self.en_us


COPY_FILE = "copy.yml"
FORM_FILE = "form.yml"
GLOBAL = "global"

ParentGroup = Literal["moderations", "notifications", "integrations"]
PARENT_GROUPS: Mapping[ParentGroup, str] = {
    "moderations": "🛡️",
    "notifications": "🔔",
    "integrations": "🔌",
}


@dataclass(frozen=True)
class Hooks:
    """What a command does on the Discord events every command shares."""

    member_joined: MemberHook | None = None
    join_stage: JoinStage = JoinStage.ANNOUNCES
    message_sent: MessageHook | None = None
    prefixed_message: PrefixHook | None = None
    message_edited: EditHook | None = None
    guild_joined: GuildJoinHook | None = None
    persistent_views: ViewsHook | None = None


@dataclass(frozen=True)
class Command:
    """One command: its key, its cogs, its hooks and its row on /setup; its copy and its
    form are the `copy.yml` and the `form.yml` beside it."""

    key: str
    cogs: tuple[type[Any], ...] = ()
    feature: FeatureModule | None = None
    hooks: Hooks = field(default_factory=Hooks)
    reminders: Mapping[str, ReminderHandler] = field(default_factory=dict)
    webhooks: Mapping[str, WebhookHandler] = field(default_factory=dict)
    opens: Opener | None = None
    group: ParentGroup | Literal[""] = ""
    on_setup: bool = False
    emoji: str = ""
    category: ParentGroup | Literal[""] = ""
    channel_permissions: tuple[str, ...] = ()
    server_permissions: tuple[str, ...] = ()
    assigns_roles: bool = False
    lazy: bool = False

    def __post_init__(self) -> None:
        if not self.category:
            object.__setattr__(self, "category", self.group)

    @property
    def answers_prefix(self) -> bool:
        """Whether this command answers messages on the server's prefix."""
        return self.hooks.prefixed_message is not None


@cache
def commands() -> tuple[Command, ...]:
    """Every command, in the order `app.commands.COMMANDS` declares them."""
    declared: Sequence[Command] = import_module("app.commands").COMMANDS
    listed = tuple(declared)

    _owned("command key", ((command.key, command) for command in listed))
    _placed(listed)
    _owned("reminder title", _reminders(listed))
    _owned("webhook event", _webhooks(listed))
    return listed


def command_for(key: str) -> Command:
    """The command declared under `key`."""
    found = next((command for command in commands() if command.key == key), None)
    if found is None:
        raise KeyError(f"{key!r} is not a command `app.commands` declares")
    return found


def copy_file(key: str) -> Path | None:
    """The `copy.yml` of the command `key`, None when its folder writes none."""
    return copy_files().get(key)


def form_file(key: str) -> Path | None:
    """The `form.yml` of the command `key`, None when the command has no form."""
    return form_files().get(key)


def copy_files() -> Mapping[str, Path]:
    """Every `copy.yml` under the key of the `COMMAND` its folder's package declares."""
    return _beside(COPY_FILE)


def form_files() -> Mapping[str, Path]:
    """Every `form.yml` under the key of the `COMMAND` its folder's package declares."""
    return _beside(FORM_FILE)


class RefusedFile(ValueError):
    """A YAML file read strictly and refused: broken, holding an anchor or an alias,
    or writing a key twice."""


def strict_yaml(text: str, name: str) -> Any:
    """The document `text` holds, refusing broken YAML, an anchor, an alias and a key
    written twice, each refusal naming the file `name`."""
    try:
        _refuse_anchors(text, name)
        _refuse_repeats(yaml.compose(text, Loader=yaml.SafeLoader), name)
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise RefusedFile(f"{name}: {error}") from error


REPOSITORY = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class YamlType:
    """One kind of YAML file Keiko reads: the files it covers, the pydantic model that
    is its schema, what it is for and the code that reads it."""

    name: str
    files: str
    schema: str
    purpose: str
    readers: tuple[str, ...]

    def model(self) -> type[BaseModel] | None:
        """The root model `schema` names as `module:Class`, imported on first use; None
        while the type waits for its schema."""
        if not self.schema:
            return None
        module, _, name = self.schema.partition(":")
        model: type[BaseModel] = getattr(import_module(module), name)
        return model

    def paths(self) -> tuple[Path, ...]:
        """Every file of the type, sorted by path."""
        return tuple(sorted(REPOSITORY.glob(self.files)))


YAML_TYPES: tuple[YamlType, ...] = (
    YamlType(
        name="form.yml",
        files="app/commands/*/form.yml",
        schema="app.forms.form.form_yaml:FormFile",
        purpose=(
            "The settings flow of one command: the screens /setup and the manager "
            "panel walk (the intro, the cards and their sections, the lists, the "
            "review), what each screen asks and the answer key it writes. A command "
            "with settings writes one beside its package."
        ),
        readers=(
            "app/forms/form/form_yaml.py: DiskSource reads it, compile_form checks it",
            "app/forms/form/form.py: the engine walks the compiled steps",
            "app/forms/form/manager.py: the manager panel draws them",
        ),
    ),
    YamlType(
        name="vocabulary.yml",
        files="app/forms/vocabulary.yml",
        schema="app.forms.form.form_yaml:Vocabulary",
        purpose=(
            "The copy every form shares: the defaults a step reads when it leaves a "
            "field out (the report footer, the review description) and the texts a "
            "form or a copy file names with ${ref.<name>}."
        ),
        readers=(
            "app/forms/form/form_yaml.py: shared_vocabulary reads it, written_out "
            "applies it before a form compiles",
            "app/core/i18n.py: read writes in each ${ref.<name>} of a copy file, and "
            "standard_footer reads the footer every embed and card falls back to",
        ),
    ),
    YamlType(
        name="copy.yml",
        files="app/commands/*/copy.yml",
        schema="app.core.copy_schema:CommandCopy",
        purpose=(
            "The copy of one command: the facts its texts read, the fixed places the "
            "shared screens fill (its slash names, its /setup row, its stats, its "
            "panel) under `command`, and every text it draws itself under `ui`, each "
            "a {en-us, pt-br} node under a key relative to the command."
        ),
        readers=(
            "app/core/i18n.py: files, read and collect load it under the command key",
            "app/core/utils.py: ml(key, locale) resolves one text",
        ),
    ),
    YamlType(
        name="languages/buttons.yml",
        files="app/languages/buttons.yml",
        schema="app.core.copy_schema:SharedButtons",
        purpose=(
            "Every button the shared layers draw (the form steps, the manager panel, "
            "the cards), each its label and the caption the panel's Help lists, read "
            "under `buttons.<name>`."
        ),
        readers=(
            "app/forms/form/actions/action.py: label and caption read one button",
            "app/forms/form/manager.py: the manager panel draws its buttons",
            "app/ui/buttons.py and app/ui/records.py: the buttons outside the forms",
        ),
    ),
    YamlType(
        name="languages/commands.yml",
        files="app/languages/commands.yml",
        schema="app.core.copy_schema:SharedCommands",
        purpose=(
            "The screens the shared layers draw for every command, under "
            "`commands.`: the shared answers and questions (admin only, cooldown, "
            "Help, the lifecycle final messages, the member pickers, the removal and "
            "discard questions) and the History list."
        ),
        readers=(
            "app/ui/embed.py: response_embed",
            "app/forms/discord/transitions.py: the form's final message",
            "app/forms/form/form.py: the member pickers and the removal and discard "
            "questions",
            "app/core/manager.py: the History lines",
        ),
    ),
    YamlType(
        name="languages/errors.yml",
        files="app/languages/errors.yml",
        schema="app.core.copy_schema:SharedErrors",
        purpose=(
            "Every error a shared layer names by its block (a validator, the form "
            "engine, the error handler), read under `errors.<name>`."
        ),
        readers=(
            "app/ui/embed.py: response_error_embed draws the red embed",
            "app/forms/form/copy.py: error_message reads the plain line",
            "app/forms/form/responses/validations.py: the validators name the error",
        ),
    ),
    YamlType(
        name="languages/texts.yml",
        files="app/languages/texts.yml",
        schema="app.core.copy_schema:SharedTexts",
        purpose=(
            "The plain lines and tables the shared layers read, under `texts.`: "
            "months, parent groups, notices, permission lines, placeholders, and "
            "the page and value words."
        ),
        readers=(
            "app/core/utils.py: ml(key, locale) resolves one text",
            "app/forms/form/copy.py: text(key, locale) resolves one text",
        ),
    ),
    YamlType(
        name="languages/locales.yml",
        files="app/languages/locales.yml",
        schema="app.core.copy_schema:LocaleNames",
        purpose=(
            "The name of each language Keiko speaks, keyed by its language code, "
            "read under `locales.<code>`."
        ),
        readers=("app/integrations/google_translate.py: get_language_with_flag",),
    ),
    YamlType(
        name="global.yml",
        files="app/core/global.yml",
        schema="app.core.facts:GlobalCatalog",
        purpose=(
            "The facts no single command owns and the context Keiko already holds: "
            "each fact a rule reads as `global.facts.<name>` and a text as `$<name>`, "
            "with its type and what it holds, and each value a text reads as "
            "`${context.<name>}`, with whether a public message may show it."
        ),
        readers=(
            "app/core/facts.py: global_catalog reads it, address and facts name a "
            "fact, with_context fills the context known at load",
            "app/core/i18n.py: the copy loader checks every rule and `$name` against "
            "it, and resolved refuses a fact it does not declare",
            "app/forms/form/form_yaml.py: compile_form refuses a `$name` or a "
            "`${context.<name>}` it does not declare",
        ),
    ),
    YamlType(
        name="catalog.yml",
        files="app/analytics/catalog.yml",
        schema="app.core.analytics:Catalog",
        purpose=(
            "Every analytics event Keiko records: its collection, its actor, the "
            "props it may carry and how it advances a server's lifetime counters."
        ),
        readers=(
            "app/core/analytics.py: catalog() reads it, record() refuses an event it "
            "does not declare",
            "app/core/analytics_writer.py: derive() and the lifetime rules",
        ),
    ),
)


SHARED_COPY: tuple[str, ...] = tuple(
    Path(kind.files).stem
    for kind in YAML_TYPES
    if Path(kind.files).parent == Path("app/languages")
)
"""The stems the shared copy files of `app/languages/` load their keys under."""


def yaml_type(name: str) -> YamlType:
    """The YAML type registered under `name`."""
    found = next((kind for kind in YAML_TYPES if kind.name == name), None)
    if found is None:
        raise KeyError(f"{name!r} is no YAML type of YAML_TYPES")
    return found


def opener(key: str) -> Opener:
    """What a button on another command's screen opens of the command `key`."""
    opens = command_for(key).opens
    if opens is None:
        raise KeyError(f"{key!r} opens nothing from another command's screen")
    return opens


def setup_features() -> tuple[Command, ...]:
    """The commands /setup and the greeting offer, in the declared order."""
    return tuple(command for command in commands() if command.on_setup)


def setup_feature(key: str) -> Command | None:
    """The command /setup offers under `key`, or None when /setup offers no such key."""
    return next((command for command in setup_features() if command.key == key), None)


def member_hooks() -> tuple[tuple[str, MemberHook], ...]:
    """The member hooks by join stage, then in the declared order."""
    staged = sorted(
        (
            (command.hooks.join_stage.value, command.key, command.hooks.member_joined)
            for command in commands()
            if command.hooks.member_joined is not None
        ),
        key=lambda entry: entry[0],
    )
    return tuple((key, hook) for _stage, key, hook in staged)


def message_hooks() -> tuple[tuple[str, MessageHook], ...]:
    """The hooks every message reaches, in the declared order."""
    return tuple(
        (command.key, command.hooks.message_sent)
        for command in commands()
        if command.hooks.message_sent is not None
    )


def prefix_hooks() -> tuple[tuple[str, PrefixHook], ...]:
    """The hooks a message on the server's prefix reaches, in the declared order."""
    return tuple(
        (command.key, command.hooks.prefixed_message)
        for command in commands()
        if command.hooks.prefixed_message is not None
    )


def edit_hooks() -> tuple[tuple[str, EditHook], ...]:
    """The hooks an edited message reaches, in the declared order."""
    return tuple(
        (command.key, command.hooks.message_edited)
        for command in commands()
        if command.hooks.message_edited is not None
    )


def guild_join_hooks() -> tuple[tuple[str, GuildJoinHook], ...]:
    """The hooks a server that added Keiko reaches, in the declared order."""
    return tuple(
        (command.key, command.hooks.guild_joined)
        for command in commands()
        if command.hooks.guild_joined is not None
    )


def view_hooks() -> tuple[tuple[str, ViewsHook], ...]:
    """The hooks that give the views Keiko listens to after a restart."""
    return tuple(
        (command.key, command.hooks.persistent_views)
        for command in commands()
        if command.hooks.persistent_views is not None
    )


def reminder_handlers() -> Mapping[str, ReminderHandler]:
    """The handler of every reminder title, each title owned by one command."""
    return _owned("reminder title", _reminders(commands()))


def webhook_handlers() -> Mapping[str, WebhookHandler]:
    """The handler of every webhook event, each event owned by one command."""
    return _owned("webhook event", _webhooks(commands()))


def _reminders(
    listed: Iterable[Command],
) -> Iterable[tuple[str, ReminderHandler]]:
    return (
        (title, handler)
        for command in listed
        for title, handler in command.reminders.items()
    )


def _webhooks(listed: Iterable[Command]) -> Iterable[tuple[str, WebhookHandler]]:
    return (
        (name, handler)
        for command in listed
        for name, handler in command.webhooks.items()
    )


def _placed(listed: Iterable[Command]) -> None:
    for command in listed:
        if command.key == GLOBAL:
            raise ValueError(
                f"no command is keyed {GLOBAL!r}: it is the owner of the facts and "
                "the context of app/core/global.yml"
            )
        if command.key in SHARED_COPY:
            raise ValueError(
                f"no command is keyed {command.key!r}: app/languages/{command.key}.yml "
                "loads the shared copy under that prefix"
            )
        for name, value in (("group", command.group), ("category", command.category)):
            if value and value not in PARENT_GROUPS:
                raise ValueError(
                    f"{command.key!r} declares the {name} {value!r}, which is no "
                    f"parent group of {list(PARENT_GROUPS)}"
                )
        if command.on_setup and not command.category:
            raise ValueError(
                f"{command.key!r} is offered on /setup in no category: give it a "
                "`group` or a `category`"
            )


@cache
def _beside(name: str) -> Mapping[str, Path]:
    folders = Path(__file__).resolve().parents[1] / "commands"
    return {
        import_module(f"app.commands.{path.parent.name}").COMMAND.key: path
        for path in sorted(folders.glob(f"*/{name}"))
    }


def _refuse_anchors(text: str, name: str) -> None:
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, NodeEvent) and event.anchor is not None:
            raise RefusedFile(
                f"{name}:{event.start_mark.line + 1}: {event.anchor} is an anchor "
                "or an alias; write the text where it is read"
            )


def _refuse_repeats(node: Any, name: str) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key, value in node.value:
            if key.value in seen:
                raise RefusedFile(
                    f"{name}:{key.start_mark.line + 1}: {key.value} twice"
                )
            seen.add(key.value)
            _refuse_repeats(value, name)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _refuse_repeats(item, name)


def _owned[Value](label: str, entries: Iterable[tuple[str, Value]]) -> dict[str, Value]:
    owned: dict[str, Value] = {}
    for name, value in entries:
        if name in owned:
            raise ValueError(f"{label} {name!r} is declared twice")
        owned[name] = value
    return owned

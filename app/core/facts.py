"""The facts a rule or a text reads, addressed `<owner>.facts.<name>`, and the context
Keiko already holds: the global catalog, the address of a fact, the facts a text
fills, and what a copy or a form text reads when it loads (its shared texts and the
context known then)."""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.commands import (
    GLOBAL,
    REPOSITORY,
    Command,
    Open,
    Text,
    commands,
    copy_file,
    strict_yaml,
)
from app.core.conditions import Leaf
from app.core.tokens import (
    Token,
    TokenError,
    did_you_mean,
    finished,
    hint,
    included,
    pieces,
    resolved,
)

CATALOG = Path(__file__).with_name("global.yml")
LANGUAGES = REPOSITORY / "app" / "languages"
LOCALES = ("en-us", "pt-br")

FactType = Literal["boolean", "enum", "number", "text"]
Facts = Mapping[str, Any]


class FactError(ValueError):
    """A fact the catalogs do not declare, a value its type does not allow, or a fact
    a block reads that the code did not pass."""


class TextError(FactError):
    """A text that cannot load, with where it is in its file and why."""

    def __init__(self, name: str, where: str, problem: str) -> None:
        self.where = where
        self.problem = problem
        super().__init__(": ".join(part for part in (name, where, problem) if part))


class Spec(BaseModel):
    """Base of the catalog's models: frozen, strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FactSpec(Spec):
    """One fact: what a rule or a `$name` of a text reads, of one type."""

    type: FactType = Field(
        description="The kind of value the code passes: `boolean`, `enum` (one of "
        "`values`), `number` or `text`.",
        json_schema_extra={"role": "identity"},
    )
    values: tuple[str, ...] | None = Field(
        None,
        description="The values an `enum` fact takes, the only ones a rule may "
        "compare it with.",
        examples=[["not_configured", "paused", "active"]],
        json_schema_extra={"role": "data"},
    )
    description: str = Field(
        description="One sentence saying what the fact holds, for whoever writes a "
        "rule or a text that reads it.",
        json_schema_extra={"role": "identity"},
    )

    @model_validator(mode="after")
    def _values_for_an_enum(self) -> FactSpec:
        if self.type == "enum" and not self.values:
            raise ValueError("an enum fact names its `values`")
        if self.type != "enum" and self.values is not None:
            raise ValueError(f"a {self.type} fact names no `values`")
        return self


class ContextEntry(Spec):
    """One value Keiko already holds in memory or in its cache, that a copy or a form
    text reads as `${context.<name>}`."""

    type: Literal["number", "text"] = Field(
        description="The kind of value the entry holds.",
        json_schema_extra={"role": "identity"},
    )
    each: Literal["command", "group"] | None = Field(
        None,
        description="Makes the entry a family read one member at a time, "
        "`${context.<name>.<key>}`: one per command key, or one per parent group; "
        "known when the copy loads.",
        json_schema_extra={"role": "data"},
    )
    public: bool = Field(
        description="Whether the value may appear in a message every member of the "
        "server sees, not only in an admin's own screens.",
        json_schema_extra={"role": "behavior"},
    )
    description: str = Field(
        description="One sentence saying what the value is and where it comes from.",
        json_schema_extra={"role": "identity"},
    )


class GlobalCatalog(Spec):
    """`app/core/global.yml`: the facts more than one command reads, and the context
    every copy and form may read."""

    facts: Open[FactSpec] = Field(
        default_factory=dict,
        description="The facts no single command owns, each read as "
        "`global.facts.<name>` by a rule and as `$<name>` or `${<name>}` by a text.",
        json_schema_extra={"role": "structure"},
    )
    context: Open[ContextEntry] = Field(
        default_factory=dict,
        description="The values Keiko already holds, each read as `${context.<name>}`.",
        json_schema_extra={"role": "structure"},
    )


@cache
def global_catalog() -> GlobalCatalog:
    """The global catalog, read once and strictly."""
    document = strict_yaml(CATALOG.read_text(encoding="utf-8"), _name(CATALOG)) or {}
    return GlobalCatalog.model_validate(document)


def shared_owners() -> frozenset[str]:
    """The prefixes of the shared copy files, whose facts are all global."""
    return frozenset(path.stem for path in LANGUAGES.glob("*.yml"))


def owner_of(key: str) -> str:
    """The owner of a copy key's facts: the command its prefix names, or `global` for
    a shared file."""
    head = key.partition(".")[0]
    return GLOBAL if head in shared_owners() else head


def address(owner: str, name: str) -> str:
    """The address of the fact `name` as `owner` reads it: global when the global
    catalog declares it, else the owner's own."""
    if name in global_catalog().facts:
        return f"{GLOBAL}.facts.{name}"
    if owner == GLOBAL:
        raise FactError(f"${name} is no fact of app/core/global.yml")
    return f"{owner}.facts.{name}"


def facts(owner: str, **values: Any) -> dict[str, Any]:
    """The facts `owner` passes, each value under its address."""
    return {address(owner, name): value for name, value in values.items()}


def short_name(fact: str) -> str:
    """The name a text writes after `$` for the fact at `fact`."""
    return fact.rpartition(".facts.")[2]


def fact_owner(fact: str) -> str:
    """The owner part of a fact's address, empty when `fact` is no address."""
    owner, separator, _name_part = fact.partition(".facts.")
    return owner if separator else ""


def filled(words: str, values: Mapping[str, Any]) -> str:
    """`words` with each fact it reads replaced by its value in `values`, in one pass,
    so a value put in is never read again; a fact with no value stays as written."""
    return resolved(
        words,
        lambda token: (
            str(values[token.name])
            if token.kind == "fact" and token.name in values
            else None
        ),
    )


def fact_hint(name: str, names: Collection[str]) -> str:
    """`; did you mean ...?` for a fact no catalog declares: the declared name it
    starts with, glued to the rest, and the closest declared name."""
    starts = sorted((known for known in names if name.startswith(known)), key=len)
    glued = [f"${{{starts[-1]}}}{name[len(starts[-1]) :]}"] if starts else []
    close = did_you_mean(name, names)
    options = [*glued, *([f"${close}"] if close else [])]
    return f"; did you mean {' or '.join(options)}?" if options else ""


def checked_value(leaf: Leaf, spec: FactSpec) -> str | None:
    """Why a rule's leaf cannot compare its fact of type `spec`, None when it can."""
    if spec.type == "boolean" and (leaf.matches is not None or leaf.count is not None):
        return "a boolean fact is tested with is, in, not_in or present"
    for value in leaf.compared():
        if spec.type == "boolean" and str(value).lower() not in ("true", "false"):
            return f"{value!r} is no boolean"
        if spec.type == "enum" and str(value) not in (spec.values or ()):
            return f"{value!r} is none of {list(spec.values or ())}"
        if spec.type == "number" and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            return f"{value!r} is no number"
    return None


def context_entry(name: str) -> ContextEntry | None:
    """The entry `${context.name}` reads, a family's for `family.member`."""
    return global_catalog().context.get(name.partition(".")[0])


def with_context(node: Any, name: str, shared: Mapping[str, Text] | None = None) -> Any:
    """A raw YAML tree with its `${ref.<name>}` texts and `${context.<family>.<member>}`
    names written in at load, refusing what the syntax or the catalogs do not allow."""
    return _written_in(node, name, shared, "")


def finished_texts(node: Any) -> Any:
    """A loaded tree with each text no later pass reads shown as it is, every `$$` a
    `$`; a text that still reads a token keeps it for the pass that fills it."""
    if isinstance(node, list):
        return [finished_texts(item) for item in node]
    if not isinstance(node, dict):
        return node
    if _is_text(node):
        return {
            locale: finished(text) if isinstance(text, str) else text
            for locale, text in node.items()
        }
    return {key: finished_texts(value) for key, value in node.items()}


def written_texts(node: Any, where: str = "") -> Iterator[tuple[str, str]]:
    """Every text of a raw tree in each locale, with its path down to the locale."""
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from written_texts(item, f"{where}[{index}]")
    elif isinstance(node, dict) and _is_text(node):
        for locale, text in node.items():
            if isinstance(text, str):
                yield _joined(where, locale), text
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from written_texts(value, _joined(where, str(key)))


def _written_in(
    node: Any, name: str, shared: Mapping[str, Text] | None, where: str
) -> Any:
    if isinstance(node, list):
        return [
            _written_in(item, name, shared, f"{where}[{index}]")
            for index, item in enumerate(node)
        ]
    if isinstance(node, str):
        return _whole_shared_text(node, name, shared, where)
    if not isinstance(node, dict):
        return node
    if set(node) == {"ref"}:
        raise TextError(
            name,
            where,
            f"{{ref: {node['ref']}}} is the old spelling: write ${{ref.{node['ref']}}}",
        )
    if _is_text(node):
        return {
            locale: _loaded(text, locale, name, shared, _joined(where, locale))
            if isinstance(text, str)
            else text
            for locale, text in node.items()
        }
    return {
        key: _written_in(value, name, shared, _joined(where, str(key)))
        for key, value in node.items()
    }


def _whole_shared_text(
    value: str, name: str, shared: Mapping[str, Text] | None, where: str
) -> Any:
    if not (value.startswith("${ref.") and value.endswith("}")):
        return value
    try:
        found = pieces(value)
    except TokenError as error:
        raise TextError(name, where, str(error)) from None
    token = found[0] if len(found) == 1 else None
    if not isinstance(token, Token) or token.kind != "ref":
        return value
    try:
        return _shared(token.name, shared).model_dump(by_alias=True)
    except FactError as error:
        raise TextError(name, where, str(error)) from None


def _loaded(
    text: str, locale: str, name: str, shared: Mapping[str, Text] | None, where: str
) -> str:
    try:
        with_shared = included(text, lambda ref: _shared(ref, shared).get(locale))
        return resolved(with_shared, lambda token: _member(token, locale), last=False)
    except (TokenError, FactError) as error:
        raise TextError(name, where, str(error)) from None


def _shared(ref: str, shared: Mapping[str, Text] | None) -> Text:
    written = f"${{ref.{ref}}}"
    if shared is None:
        raise FactError(f"{written}: a shared text names no other shared text")
    found = shared.get(ref)
    if found is None:
        closest = hint(ref, shared, lambda known: f"${{ref.{known}}}")
        raise FactError(f"{written} is no text of app/forms/vocabulary.yml{closest}")
    return found


def _member(token: Token, locale: str) -> str | None:
    entry = context_entry(token.name) if token.kind == "context" else None
    if entry is None or entry.each is None:
        return None
    family, _dot, member = token.name.partition(".")
    known = _members(entry.each)
    if member not in known:
        listed = f" (known: {', '.join(sorted(known))})"
        closest = hint(member, known, lambda close: f"${{context.{family}.{close}}}")
        raise FactError(
            f"{token.written}: {family} has no {member or 'member'}{closest or listed}"
        )
    return known[member][locale]


def _is_text(node: Mapping[Any, Any]) -> bool:
    return bool(node) and set(node) <= set(LOCALES)


def _joined(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key


@cache
def _members(each: str) -> dict[str, dict[str, str]]:
    groups = _group_names()
    if each == "group":
        return {
            group: {locale: _slash_path((names,), locale) for locale in LOCALES}
            for group, names in groups.items()
        }
    found: dict[str, dict[str, str]] = {}
    for command in commands():
        slash = _slash(command)
        if "name" in slash:
            parts = (groups.get(command.group, {}), slash.get("subgroup") or {})
            named = (*parts, slash["name"])
            found[command.key] = {
                locale: _slash_path(named, locale) for locale in LOCALES
            }
    return found


def _slash_path(parts: tuple[Mapping[str, str], ...], locale: str) -> str:
    return "/" + " ".join(part[locale] for part in parts if part.get(locale))


def _group_names() -> dict[str, dict[str, str]]:
    path = LANGUAGES / "texts.yml"
    document = strict_yaml(path.read_text(encoding="utf-8"), _name(path)) or {}
    return dict(document.get("groups") or {})


def _slash(command: Command) -> dict[str, Any]:
    path = copy_file(command.key)
    if path is None:
        return {}
    document = strict_yaml(path.read_text(encoding="utf-8"), _name(path)) or {}
    return dict((document.get("command") or {}).get("slash") or {})


def _name(path: Path) -> str:
    return str(
        path.relative_to(REPOSITORY) if path.is_relative_to(REPOSITORY) else path
    )

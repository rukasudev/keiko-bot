"""The facts a rule or a text reads, addressed `<owner>.facts.<name>`, and the context
Keiko already holds: the global catalog, the address of a fact, the `$` names of a
text, and the context a copy or a form text is resolved with when it is read."""

from __future__ import annotations

import re
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
    commands,
    copy_file,
    strict_yaml,
)
from app.core.conditions import Leaf

CATALOG = Path(__file__).with_name("global.yml")
LANGUAGES = REPOSITORY / "app" / "languages"
LOCALES = ("en-us", "pt-br")
DOLLAR = re.compile(r"\$([a-z][a-z0-9_]*)")
CONTEXT = re.compile(r"\{context:([a-z_]+(?:\.[a-z_]+)?)\}")

FactType = Literal["boolean", "enum", "number", "text"]
Facts = Mapping[str, Any]


class FactError(ValueError):
    """A fact the catalogs do not declare, a value its type does not allow, or a fact
    a block reads that the code did not pass."""


class Spec(BaseModel):
    """Base of the catalog's models: frozen, strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FactSpec(Spec):
    """One fact: what a rule or a `$name` of copy reads, of one type."""

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
    text reads as `{context:<name>}`."""

    type: Literal["number", "text"] = Field(
        description="The kind of value the entry holds.",
        json_schema_extra={"role": "identity"},
    )
    each: Literal["command", "group"] | None = Field(
        None,
        description="Makes the entry a family read one member at a time, "
        "`{context:<name>.<key>}`: one per command key, or one per parent group; "
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
        "`global.facts.<name>` by a rule and as `$<name>` by a text.",
        json_schema_extra={"role": "structure"},
    )
    context: Open[ContextEntry] = Field(
        default_factory=dict,
        description="The values Keiko already holds, each read as `{context:<name>}`.",
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
    """`words` with every `$name` replaced by its value, in one pass and the longest
    name first, so `$page` never eats the start of `$pages`."""
    if not values:
        return words
    names = "|".join(re.escape(name) for name in sorted(values, key=len, reverse=True))
    return re.sub(
        r"\$(" + names + r")", lambda found: str(values[found.group(1)]), words
    )


def placeholders(words: str, names: Collection[str]) -> tuple[list[str], list[str]]:
    """The declared names `words` fills, each `$token` read as the longest declared
    name it starts with, the way `fill` replaces it, and the tokens no declared name
    starts."""
    found: list[str] = []
    unknown: list[str] = []
    for token in DOLLAR.findall(words):
        starts = [name for name in names if token.startswith(name)]
        if starts:
            found.append(max(starts, key=len))
        else:
            unknown.append(token)
    return found, unknown


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
    """The entry `{context:name}` reads, a family's for `family.member`."""
    return global_catalog().context.get(name.partition(".")[0])


def known_at_load(name: str) -> bool:
    """Whether `{context:name}` names a member of a family the loader fills."""
    entry = context_entry(name)
    return entry is not None and entry.each is not None


def with_context(node: Any, name: str) -> Any:
    """A raw YAML tree with every `{context:<family>.<member>}` of its texts replaced
    by the value of that locale; the context filled at render stays as written."""
    if isinstance(node, list):
        return [with_context(item, name) for item in node]
    if not isinstance(node, dict):
        return node
    if set(node) <= set(LOCALES) and node:
        return {
            locale: _members_written(text, locale, name)
            if isinstance(text, str)
            else text
            for locale, text in node.items()
        }
    return {key: with_context(value, name) for key, value in node.items()}


def context_tokens(words: str) -> Iterator[str]:
    """The name of every `{context:...}` token a text writes."""
    yield from CONTEXT.findall(words)


def _members_written(text: str, locale: str, name: str) -> str:
    def value(found: re.Match[str]) -> str:
        token = found.group(1)
        entry = context_entry(token)
        if entry is None or entry.each is None:
            return found.group(0)
        family, _dot, member = token.partition(".")
        known = _members(entry.each)
        if member not in known:
            raise FactError(
                f"{name}: {{context:{token}}}: {family} has no {member or 'member'} "
                f"(known: {', '.join(sorted(known))})"
            )
        return known[member][locale]

    return CONTEXT.sub(value, text)


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

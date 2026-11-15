"""The copy loader: the shared files in app/languages plus each command's `copy.yml`,
each read against its schema."""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar, overload

import i18n
from i18n import translations
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    model_validator,
)

from app.constants import supported_locales
from app.core.commands import RefusedFile, Text, commands, copy_file, strict_yaml

APP = Path(__file__).resolve().parents[1]
LOCALE_NAME = re.compile(r"[a-z]{2}-[a-z]{2}")
SNAKE_KEY = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
BRACED = re.compile(r"\{([A-Za-z_][\w-]*)\}")

Block = TypeVar("Block", bound=BaseModel)


class CopyError(Exception):
    """A copy file the loader refuses: broken YAML, an anchor or an alias, a key
    defined twice, a text not written in every locale, or a file its schema
    refuses."""


def _named_by_dollar(text: Text) -> Text:
    for locale, words in (("en-us", text.en_us), ("pt-br", text.pt_br)):
        found = BRACED.search(words)
        if found:
            raise ValueError(
                f"the {locale} text writes {found.group(0)}: copy names a value "
                f"as ${found.group(1)}"
            )
    return text


CopyText = Annotated[Text, AfterValidator(_named_by_dollar)]
"""A text of copy: `$name` marks each value the code fills, and a `{name}` is refused,
since braces belong to the templates an admin writes."""


class CopyBlock(BaseModel):
    """Base of every copy model: frozen, and refusing a key its schema does not name."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Variant(CopyBlock):
    """What a block writes in one state the code picks, in place of its own title or
    description."""

    title: CopyText | None = Field(
        None,
        description="The title drawn in this variant instead of the block's; absent "
        "keeps the block's title.",
        json_schema_extra={"role": "copy"},
    )
    description: CopyText | None = Field(
        None,
        description="The description drawn in this variant instead of the block's; "
        "absent keeps the block's description.",
        json_schema_extra={"role": "copy"},
    )

    @model_validator(mode="after")
    def _writes_something(self) -> Variant:
        if self.title is None and self.description is None:
            raise ValueError("a variant writes its title, its description or both")
        return self


class Plural(CopyBlock):
    """One text per plural form of a count the code fills: `one` for a count of one,
    `other` for every other count."""

    one: CopyText = Field(
        description="The text for a count of exactly one.",
        json_schema_extra={"role": "copy"},
    )
    other: CopyText = Field(
        description="The text for every other count.",
        json_schema_extra={"role": "copy"},
    )

    def for_count(self, count: int) -> Text:
        """The text `count` reads: `one` for exactly one, else `other`."""
        return self.one if count == 1 else self.other


def locales() -> list[str]:
    """The locales the copy exists in, as the files name them."""
    return [locale.lower() for locale in supported_locales]


def files() -> list[tuple[Path, str]]:
    """Every copy file with the prefix its keys load under, the shared ones first,
    then each command's `copy.yml`."""
    shared = [(path, path.stem) for path in sorted((APP / "languages").glob("*.yml"))]
    owned = [
        (path, command.key)
        for command in commands()
        if (path := copy_file(command.key)) is not None
    ]
    return [*shared, *owned]


def read(path: Path) -> dict[str, Any]:
    """The copy of one file, strictly parsed: no anchor, no alias, no key written
    twice; every `{ref: name}` is the text of app/forms/vocabulary.yml it names."""
    name = _name(path)
    try:
        document = strict_yaml(path.read_text(encoding="utf-8"), name)
    except RefusedFile as error:
        raise CopyError(str(error)) from error
    if not isinstance(document, dict):
        raise CopyError(f"{name}: no copy block in it")
    resolved = _resolved(document, name, "")
    assert isinstance(resolved, dict)
    return resolved


def check(path: Path, prefix: str) -> BaseModel:
    """The file read against its schema: a command's `copy.yml` against `CommandCopy`,
    a shared file against its own; every key snake_case."""
    from app.core.copy_schema import validated

    name = _name(path)
    written(path, prefix)
    document = read(path)
    _snake_keys(document, name, "")
    try:
        return validated(document, prefix, path.parent == APP / "languages")
    except ValidationError as error:
        raise CopyError(f"{name}: {_first(error)}") from None
    except ValueError as error:
        raise CopyError(f"{name}: {error}") from None


def texts(
    node: dict[str, Any], prefix: str, name: str
) -> Iterator[tuple[str, dict[str, str]]]:
    """Every text below a block with its words in each locale, refusing a text that
    misses a locale or carries one Keiko does not speak."""
    for key, value in node.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if not isinstance(value, dict):
            raise CopyError(f"{name}: {full} is a text with no locale")
        if any(LOCALE_NAME.fullmatch(str(entry)) for entry in value):
            yield full, _bilingual(value, full, name)
        else:
            yield from texts(value, full, name)


def written(path: Path, prefix: str) -> list[tuple[str, dict[str, str]]]:
    """Every text one file loads under `prefix`, with its words in each locale; a
    null loads no text."""
    document = _without_nulls(read(path))
    return list(texts(document, prefix, _name(path)))


def collect(locale: str) -> dict[str, Any]:
    """Every key of a locale with its value, refusing a key defined twice and a file
    its schema refuses."""
    values: dict[str, Any] = {}
    owners: dict[str, Path] = {}
    branches: dict[str, Path] = {}
    for path, prefix in files():
        for key, words in written(path, prefix):
            _claim(key, path, owners, branches)
            values[key] = words[locale]
        check(path, prefix)
    return values


def load() -> None:
    """Load every copy file once; a later miss never reads a file again."""
    typed.cache_clear()
    for locale in locales():
        for key, value in collect(locale).items():
            i18n.add_translation(key, value, locale=locale)
    typed()
    i18n.load_path.clear()
    i18n.set("fallback", "en")


def keys(locale: str) -> dict[str, Any]:
    """Every loaded key of a locale with its value."""
    return dict(translations.container.get(locale, {}))


@cache
def typed() -> dict[str, BaseModel]:
    """The typed copy of every file, by the prefix its keys load under."""
    return {prefix: check(path, prefix) for path, prefix in files()}


@overload
def block(key: str, kind: type[Block], required: Literal[True] = True) -> Block: ...


@overload
def block(key: str, kind: type[Block], required: Literal[False]) -> Block | None: ...


def block(key: str, kind: type[Block], required: bool = True) -> Block | None:
    """The typed block under `key`; a key no copy file writes raises, or is None when
    the block is not `required`."""
    head, _, rest = key.partition(".")
    node: Any = typed().get(head)
    for part in rest.split(".") if rest else ():
        if node is None:
            break
        node = _child(node, part, key)
    if node is None:
        if required:
            raise CopyError(f"{key}: no copy file writes it")
        return None
    if not isinstance(node, kind):
        raise CopyError(f"{key} is a {type(node).__name__}, not a {kind.__name__}")
    return node


def _child(node: Any, part: str, key: str) -> Any:
    if isinstance(node, RootModel):
        node = node.root
    if isinstance(node, dict):
        return node.get(part)
    if isinstance(node, BaseModel) and part in type(node).model_fields:
        return getattr(node, part)
    raise CopyError(f"{key}: {type(node).__name__} has no {part}")


def _resolved(node: Any, name: str, where: str) -> Any:
    if not isinstance(node, dict):
        return node
    if set(node) == {"ref"}:
        found = _vocabulary().get(str(node["ref"]))
        if found is None:
            raise CopyError(
                f"{name}: {where}: {node['ref']!r} is no text of "
                "app/forms/vocabulary.yml"
            )
        return found
    return {
        key: _resolved(value, name, f"{where}.{key}" if where else str(key))
        for key, value in node.items()
    }


def standard_footer() -> Text:
    """The standard /report line, written once as the footer every form step falls
    back to in app/forms/vocabulary.yml, and closing every embed that writes none."""
    from app.forms.form.form_yaml import shared_vocabulary

    return shared_vocabulary().fallbacks.footer


@cache
def _vocabulary() -> dict[str, dict[str, str]]:
    from app.forms.form.form_yaml import shared_vocabulary

    return {
        name: text.model_dump(by_alias=True)
        for name, text in shared_vocabulary().texts.items()
    }


def _snake_keys(node: dict[str, Any], name: str, where: str) -> None:
    if set(node) <= set(locales()):
        return
    for key, value in node.items():
        full = f"{where}.{key}" if where else str(key)
        if not SNAKE_KEY.fullmatch(str(key)):
            raise CopyError(f"{name}: {full}: a copy key is snake_case")
        if isinstance(value, dict):
            _snake_keys(value, name, full)


def _without_nulls(node: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _without_nulls(value) if isinstance(value, dict) else value
        for key, value in node.items()
        if value is not None
    }


def _first(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"]) or "the file"
    reason = "unknown key" if first["type"] == "extra_forbidden" else first["msg"]
    return f"{where}: {reason}"


def _bilingual(node: dict[Any, Any], key: str, name: str) -> dict[str, str]:
    spoken = locales()
    problems = [f"no {locale}" for locale in spoken if locale not in node]
    problems += [
        f"{entry} is no locale of {spoken}" for entry in node if entry not in spoken
    ]
    problems += [
        f"the {locale} text is no string"
        for locale, text in node.items()
        if locale in spoken and not isinstance(text, str)
    ]
    if problems:
        raise CopyError(f"{name}: {key}: {', '.join(problems)}")
    return {str(locale): text for locale, text in node.items()}


def _claim(
    key: str, path: Path, owners: dict[str, Path], branches: dict[str, Path]
) -> None:
    here = _name(path)
    if key in owners:
        raise CopyError(f"{key}: defined in {_name(owners[key])} and in {here}")
    if key in branches:
        raise CopyError(f"{key}: a block in {_name(branches[key])}, a key in {here}")
    parts = key.split(".")
    for node in (".".join(parts[:size]) for size in range(1, len(parts))):
        if node in owners:
            raise CopyError(
                f"{node}: a key in {_name(owners[node])}, a block in {here}"
            )
        branches.setdefault(node, path)
    owners[key] = path


def _name(path: Path) -> str:
    return str(
        path.relative_to(APP.parent) if path.is_relative_to(APP.parent) else path
    )

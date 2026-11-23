"""The copy loader: the shared files in app/languages plus each command's `copy.yml`,
each read against its schema."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar, get_origin, overload

import i18n
from i18n import translations
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
)

from app.constants import supported_locales
from app.core.commands import (
    GLOBAL,
    RefusedFile,
    Text,
    commands,
    copy_file,
    strict_yaml,
)
from app.core.conditions import Override, Scope, When, leaves, winner
from app.core.facts import (
    FactError,
    Facts,
    FactSpec,
    address,
    checked_value,
    context_entry,
    fact_hint,
    fact_owner,
    filled,
    finished_texts,
    global_catalog,
    owner_of,
    short_name,
    with_context,
    written_texts,
)
from app.core.tokens import Token, TokenError, fact_names, hint, plain, tokens

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
        found = BRACED.search(plain(words))
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
    """The copy of one file, strictly parsed (no anchor, no alias, no key written
    twice), with what its texts read at load written in: each `${ref.<name>}` the
    text of app/forms/vocabulary.yml it names, each command and group of
    `${context...}` its slash name."""
    name = _name(path)
    try:
        document = strict_yaml(path.read_text(encoding="utf-8"), name)
    except RefusedFile as error:
        raise CopyError(str(error)) from error
    if not isinstance(document, dict):
        raise CopyError(f"{name}: no copy block in it")
    try:
        loaded = with_context(document, name, _vocabulary())
    except FactError as error:
        raise CopyError(str(error)) from None
    assert isinstance(loaded, dict)
    return loaded


def check(path: Path, prefix: str) -> BaseModel:
    """The file read against its schema: a command's `copy.yml` against `CommandCopy`,
    a shared file against its own; every key snake_case."""
    from app.core.copy_schema import validated

    name = _name(path)
    written(path, prefix)
    loaded = read(path)
    _snake_keys(loaded, name, "")
    shared = path.parent == APP / "languages"
    _tokens_checked(loaded, GLOBAL if shared else prefix, name)
    try:
        copy = validated(finished_texts(loaded), prefix, shared)
    except ValidationError as error:
        raise CopyError(f"{name}: {_first(error)}") from None
    except ValueError as error:
        raise CopyError(f"{name}: {error}") from None
    _facts_checked(copy, GLOBAL if shared else prefix, name)
    return copy


def texts(
    node: dict[str, Any], prefix: str, name: str
) -> Iterator[tuple[str, dict[str, str]]]:
    """Every text below a block with its words in each locale, refusing a text that
    misses a locale or carries one Keiko does not speak."""
    for key, value in node.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, list):
            continue
        if not isinstance(value, dict):
            if key in setting_keys() and isinstance(value, (str, bool)):
                continue
            raise CopyError(f"{name}: {full} is a text with no locale")
        if any(LOCALE_NAME.fullmatch(str(entry)) for entry in value):
            yield full, _bilingual(value, full, name)
        else:
            yield from texts(value, full, name)


@cache
def setting_keys() -> frozenset[str]:
    """The keys copy writes as a closed setting, not as words (`tone`, `card`): every
    field of a copy block typed as a closed set of values or as a yes or no."""
    import app.core.copy_schema  # noqa: F401

    found: set[str] = set()
    pending = list(CopyBlock.__subclasses__())
    while pending:
        model = pending.pop()
        pending.extend(model.__subclasses__())
        for field_name, info in model.model_fields.items():
            if info.annotation is bool or get_origin(info.annotation) is Literal:
                found.add(field_name)
    return frozenset(found)


def written(path: Path, prefix: str) -> list[tuple[str, dict[str, str]]]:
    """Every text one file loads under `prefix`, with its words in each locale; a
    null loads no text."""
    document = _without_nulls(finished_texts(read(path)))
    document.pop("facts", None)
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


def fact_specs(owner: str) -> Mapping[str, FactSpec]:
    """The facts `owner` declares: a command's in its `copy.yml`, or the global ones."""
    if owner == GLOBAL:
        return global_catalog().facts
    copy = typed().get(owner)
    declared: Mapping[str, FactSpec] = getattr(copy, "facts", None) or {}
    return declared


def resolved(
    key: str, kind: type[Block], given: Facts | None = None
) -> tuple[Block, dict[str, Any]]:
    """The block under `key` with the overrides `given` chooses in place, and the value
    of each `$name` it fills; refuses a fact the block reads that is not given, and a
    given fact neither its command nor the global catalog declares."""
    found = block(key, kind)
    owner = owner_of(key)
    passed = dict(given or {})
    unknown = sorted(fact for fact in passed if not _declared(fact, owner))
    missing = sorted(set(needs(found, owner)) - set(passed))
    if unknown:
        raise CopyError(f"{key}: {', '.join(unknown)} is no fact of {owner} or global")
    if missing:
        raise CopyError(
            f"{key} reads {', '.join(missing)}, which the code did not pass"
        )
    values = {short_name(fact): value for fact, value in passed.items()}
    return _overridden(found, Scope(passed)), values


def resolved_texts(key: str, given: Facts | None, locale: str) -> dict[str, str]:
    """Each text a block writes at its top level, in `locale`, with the overrides
    `given` chooses and its `$name`s filled; a text it leaves out is absent."""
    found, values = resolved(key, BaseModel, given)
    return {
        name: filled(text.get(locale), values)
        for name in type(found).model_fields
        if isinstance(text := getattr(found, name), Text)
    }


def needs(node: BaseModel, owner: str) -> list[str]:
    """The address of every fact a block reads: the key of each rule of its overrides
    and each `$name` of its texts, its `lines` and `rows` left to the code that fills
    them one by one."""
    names = {*fact_specs(owner), *global_catalog().facts}
    found = [leaf.key for _path, rule in _rules(node) for leaf in leaves(rule)]
    for _path, text in _texts(node, skipped=("lines", "rows")):
        for words in (text.en_us, text.pt_br):
            found += [
                address(owner, name) for name in fact_names(words) if name in names
            ]
    return sorted(set(found))


def _declared(fact: str, owner: str) -> bool:
    named = fact_owner(fact)
    return named in (owner, GLOBAL) and short_name(fact) in fact_specs(named)


def _overridden(node: Block, scope: Scope) -> Block:
    updates: dict[str, Any] = {}
    for name in type(node).model_fields:
        value = getattr(node, name)
        if isinstance(value, dict) and any(
            isinstance(child, BaseModel) and not isinstance(child, Text)
            for child in value.values()
        ):
            updates[name] = {
                child_key: _overridden(child, scope)
                if isinstance(child, BaseModel)
                else child
                for child_key, child in value.items()
            }
    chosen = winner(getattr(node, "overrides", ()), scope)
    if chosen is not None:
        updates.update(chosen.written())
    return node.model_copy(update=updates) if updates else node


def _facts_checked(copy: BaseModel, owner: str, name: str) -> None:
    own = dict(getattr(copy, "facts", None) or {}) if owner != GLOBAL else {}
    shared = global_catalog().facts
    twice = sorted(set(own) & set(shared))
    if twice:
        raise CopyError(
            f"{name}: facts.{twice[0]}: app/core/global.yml declares it too, and a "
            "short name has one owner"
        )
    for path, rule in _rules(copy):
        for leaf in leaves(rule):
            problem = _leaf_problem(leaf, owner, own)
            if problem:
                raise CopyError(f"{name}: {path}.when: {leaf.key}: {problem}")


def _tokens_checked(document: Mapping[str, Any], owner: str, name: str) -> None:
    own = {} if owner == GLOBAL else dict(document.get("facts") or {})
    names = [*own, *global_catalog().facts]
    where = "app/core/global.yml" if owner == GLOBAL else f"{owner} or global"
    body = {key: value for key, value in document.items() if key != "facts"}
    for path, words in written_texts(body):
        try:
            found = tokens(words)
        except TokenError as error:
            raise CopyError(f"{name}: {path}: {error}") from None
        for token in found:
            problem = _token_problem(token, names, where)
            if problem:
                raise CopyError(f"{name}: {path}: {problem}")


def _token_problem(token: Token, names: list[str], where: str) -> str:
    if token.kind == "fact" and token.name not in names:
        return f"{token.written} is no fact of {where}{fact_hint(token.name, names)}"
    if token.kind == "context" and context_entry(token.name) is None:
        declared = global_catalog().context
        closest = hint(token.name, declared, lambda close: f"${{context.{close}}}")
        return f"{token.written} is no context entry of app/core/global.yml{closest}"
    if token.kind == "context":
        return (
            f"{token.written} is no context a copy reads (only a family known at "
            "load: commands, groups)"
        )
    if token.kind in ("answer", "plural"):
        return f"{token.written}: a copy reads no answer, only a form does"
    return ""


def _leaf_problem(leaf: Any, owner: str, own: Mapping[str, FactSpec]) -> str:
    named = fact_owner(leaf.key)
    if named not in (owner, GLOBAL):
        return f"a copy rule reads a fact of {owner} or global (`<owner>.facts.<name>`)"
    specs = global_catalog().facts if named == GLOBAL else own
    spec = specs.get(short_name(leaf.key))
    if spec is None:
        return f"no such fact in {named}"
    return checked_value(leaf, spec) or ""


def _texts(node: Any, skipped: tuple[str, ...] = ()) -> Iterator[tuple[str, Text]]:
    for path, found in _nodes(node, "", ("facts", "when", *skipped)):
        if isinstance(found, Text):
            yield path, found


def _rules(node: Any) -> Iterator[tuple[str, When]]:
    for path, found in _nodes(node, "", ("facts", "when")):
        if isinstance(found, Override):
            yield path, found.when


def _nodes(node: Any, path: str, skipped: tuple[str, ...]) -> Iterator[tuple[str, Any]]:
    yield path, node
    if isinstance(node, RootModel):
        yield from _nodes(node.root, path, skipped)
    elif isinstance(node, BaseModel) and not isinstance(node, Text):
        for name in type(node).model_fields:
            if name not in skipped:
                child = getattr(node, name)
                yield from _nodes(child, f"{path}.{name}" if path else name, skipped)
    elif isinstance(node, dict):
        for key, child in node.items():
            yield from _nodes(child, f"{path}.{key}" if path else str(key), skipped)
    elif isinstance(node, tuple):
        for index, child in enumerate(node):
            yield from _nodes(child, f"{path}[{index}]", skipped)


def _child(node: Any, part: str, key: str) -> Any:
    if isinstance(node, RootModel):
        node = node.root
    if isinstance(node, dict):
        return node.get(part)
    if isinstance(node, BaseModel) and part in type(node).model_fields:
        return getattr(node, part)
    raise CopyError(f"{key}: {type(node).__name__} has no {part}")


def standard_footer() -> Text:
    """The standard /report line, written once as the footer every form step falls
    back to in app/forms/vocabulary.yml, and closing every embed that writes none."""
    from app.forms.form.form_yaml import shared_vocabulary

    footer = shared_vocabulary().fallbacks.footer
    return Text.model_validate(finished_texts(footer.model_dump(by_alias=True)))


def _vocabulary() -> Mapping[str, Text]:
    from app.forms.form.form_yaml import shared_vocabulary

    return shared_vocabulary().texts


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

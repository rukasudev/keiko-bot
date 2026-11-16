"""The YAML reference as one static page: the schema of every YAML type, a real file
of each and the Portuguese of every text, written as data the template draws in two
views, the Reference and the Tree."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.keiko.yamldoc.files import Reading, Tag, Use
from scripts.keiko.yamldoc.schema import Field, Kinds, Model, Schema, Shape, Target
from scripts.keiko.yamldoc.translations import Translations, plain

from app.core.commands import YamlType

TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "yamldoc"
TEMPLATE = TEMPLATES / "page.html"
THEME = TEMPLATES / "theme.css"
THEMED = "/*theme*/"
DATA = "<!--data-->"
SLUG = re.compile(r"[^a-z0-9]+")
SCALARS = {
    "string": "text",
    "boolean": "boolean",
    "integer": "number",
    "number": "number",
}
COMPACT_EXAMPLE = 6

Payload = dict[str, Any]


@dataclass(frozen=True)
class Documented:
    """One YAML type, the text of each of its files by path, its schema (None while
    it waits for one) and what its files write."""

    kind: YamlType
    files: Mapping[str, str]
    schema: Schema | None
    reading: Reading


def render(documents: Sequence[Documented], translations: Translations) -> str:
    """The whole reference page for `documents`, in registry order."""
    data = json.dumps(
        payload(documents, translations),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return styled(TEMPLATE).replace(DATA, data.replace("</", "<\\/"))


def styled(template: Path) -> str:
    """The page template at `template` with the theme every docs page shares."""
    theme = THEME.read_text(encoding="utf-8").rstrip("\n")
    return template.read_text(encoding="utf-8").replace(THEMED, theme)


def payload(documents: Sequence[Documented], translations: Translations) -> Payload:
    """What the page draws: each type, its schema, a real file and its texts."""
    return {"types": [_Type(document, translations).build() for document in documents]}


def slug(text: str) -> str:
    """The anchor form of `text`: lower-case letters, digits and hyphens."""
    return SLUG.sub("-", text.lower()).strip("-")


class _Type:
    def __init__(self, document: Documented, translations: Translations) -> None:
        self.document = document
        self.name = document.kind.name
        self.translations = translations
        reading = document.reading
        self.uses: dict[tuple[str, str], list[Use]] = {}
        for use in sorted(reading.uses, key=_place):
            self.uses.setdefault((use.model, use.field), []).append(use)
        self.tags: dict[tuple[str, str], list[Tag]] = {}
        for tag in sorted(reading.tags, key=_place):
            self.tags.setdefault((tag.kinds, tag.value), []).append(tag)
        self.notes: dict[tuple[str, str], set[str]] = {}
        for note in reading.notes:
            self.notes.setdefault((note.model, note.field), set()).add(note.comment)

    def build(self) -> Payload:
        kind = self.document.kind
        out: Payload = {
            "id": slug(kind.name),
            "name": kind.name,
            "glob": kind.files,
            "schema": kind.schema,
            "files": list(self.document.files),
            "purpose": self.text(kind.name, kind.purpose),
            "readers": list(kind.readers),
        }
        schema = self.document.schema
        if schema is not None:
            out |= {
                "root": _shape(schema.root),
                "to": _target(schema.target(schema.root)),
                "models": [
                    self.model(schema, schema.models[name]) for name in schema.reached
                ],
                "kinds": [
                    self.kinds(schema.kinds[name]) for name in schema.kinds_reached
                ],
                "sample": self.sample(),
            }
        return _compact(out)

    def text(self, key: str, english: str) -> list[str]:
        if not english:
            return []
        portuguese = self.translations.portuguese(key, english)
        return [plain(english), portuguese] if portuguese else [plain(english)]

    def model(self, schema: Schema, model: Model) -> Payload:
        kinds = schema.member_of(model.name)
        first = kinds.key if kinds else ""
        return _compact(
            {
                "n": model.name,
                "d": self.text(f"{self.name}/{model.name}", model.description),
                "closed": model.closed,
                "text": model.bilingual(),
                "f": [
                    self.field(schema, model, field, kinds)
                    for field in model.ordered(first)
                ],
            }
        )

    def field(
        self, schema: Schema, model: Model, field: Field, kinds: Kinds | None
    ) -> Payload:
        owners = [model.name]
        if kinds is not None and field.name in kinds.shared:
            owners = [member.model for member in kinds.members]
        uses = sorted(
            (use for owner in owners for use in self.uses.get((owner, field.name), ())),
            key=_place,
        )
        notes = set().union(
            *(self.notes.get((owner, field.name), set()) for owner in owners)
        )
        return _compact(
            {
                "n": field.name,
                "s": field.segment,
                "t": _shape(field.shape),
                "mk": _mark(schema, field.shape),
                "q": field.required,
                "r": field.role,
                "d": self.text(
                    f"{self.name}/{model.name}.{field.name}", field.description
                ),
                "df": field.default,
                "ex": list(field.examples),
                "ru": [[rule.keyword, rule.value, rule.each] for rule in field.rules],
                "to": _target(schema.target(field.shape)),
                "u": [len(uses), len({use.path for use in uses})] if uses else None,
                "x": _example(uses),
                "o": sorted(notes),
            }
        )

    def kinds(self, kinds: Kinds) -> Payload:
        members = []
        for member in kinds.members:
            tags = sorted(
                (
                    tag
                    for value in member.values
                    for tag in self.tags.get((kinds.name, value), ())
                ),
                key=_place,
            )
            members.append(
                _compact(
                    {
                        "tag": member.tag,
                        "v": list(member.values),
                        "model": member.model,
                        "u": len({tag.path for tag in tags}),
                        "x": _example(tags),
                    }
                )
            )
        return {
            "n": kinds.name,
            "key": kinds.key,
            "shared": list(kinds.shared),
            "m": members,
        }

    def sample(self) -> Payload | None:
        files = self.document.files
        if not files:
            return None
        written: Counter[str] = Counter()
        for uses in self.uses.values():
            written.update({use.path for use in uses})
        path = min(files, key=lambda item: (-written[item], item))
        marks = sorted(
            [use.line, use.end, use.trail, f"{use.model}.{use.field}"]
            for use in self.document.reading.uses
            if use.path == path
        )
        return {"file": path, "lines": files[path].splitlines(), "marks": marks}


def _shape(shape: Shape) -> Payload:
    return _compact(
        {
            "k": shape.kind,
            "n": shape.name,
            "v": list(shape.values),
            "c": [_shape(child) for child in shape.children],
            "null": shape.nullable,
        }
    )


def _mark(schema: Schema, shape: Shape) -> str:
    if shape.kind == "list":
        return "list"
    if shape.kind == "open":
        return "map"
    if shape.kind == "model":
        return "i18n" if schema.models[shape.name].bilingual() else "object"
    if shape.kind == "kinds":
        return "object"
    if shape.kind in ("enum", "const"):
        return "enum"
    if shape.kind == "scalar":
        return SCALARS.get(shape.name, "value")
    structural = all(child.kind in ("model", "kinds") for child in shape.children)
    if shape.kind == "one_of" and structural and schema.target(shape) is not None:
        return "object"
    return "value"


def _target(target: Target | None) -> Payload | None:
    if target is None:
        return None
    found: Payload = {"k": target.kind, "n": target.name}
    if target.kind == "shapes":
        found["m"] = list(target.names)
    return found


def _example(found: Iterable[Use | Tag]) -> Payload | None:
    items = list(found)
    if not items:
        return None
    best = min(
        items,
        key=lambda item: (
            item.snippet.count("\n") >= COMPACT_EXAMPLE,
            item.path,
            item.line,
        ),
    )
    return {"f": best.path, "l": best.line, "c": best.snippet}


def _place(item: Use | Tag) -> tuple[str, int]:
    return (item.path, item.line)


def _compact(found: Payload) -> Payload:
    return {
        key: value
        for key, value in found.items()
        if value is not None and value is not False and value != "" and value != []
    }

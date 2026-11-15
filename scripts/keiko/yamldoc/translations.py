"""The Portuguese of the YAML reference: one file, `docs/yaml/pt-br.yml`, keyed by
what each entry translates, holding the English it translated beside it so a changed
English reads as stale.

    form.yml                     the purpose of the type
    form.yml/FormFile            the description of a model
    form.yml/FormFile.version    the description of a field
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import yaml
from scripts.keiko.yamldoc.schema import Schema

from app.core.commands import REPOSITORY

TRANSLATIONS = REPOSITORY / "docs" / "yaml" / "pt-br.yml"
ENGLISH = "en-us"
PORTUGUESE = "pt-br"

Gap = Literal["missing", "stale"]


@dataclass(frozen=True)
class Translation:
    """The English a text had when it was translated, and its Portuguese."""

    english: str
    portuguese: str


@dataclass(frozen=True)
class Translations:
    """Every translation of the reference, by key."""

    entries: Mapping[str, Translation] = field(default_factory=dict)

    def portuguese(self, key: str, english: str) -> str | None:
        """The Portuguese of `english` under `key`; None when missing or stale."""
        entry = self.entries.get(key)
        if entry is None or not entry.portuguese or entry.english != plain(english):
            return None
        return entry.portuguese

    def gaps(self, texts: Mapping[str, str]) -> dict[str, Gap]:
        """The keys of `texts` with no current translation, each missing or stale."""
        found: dict[str, Gap] = {}
        for key, english in sorted(texts.items()):
            entry = self.entries.get(key)
            if entry is None or not entry.portuguese:
                found[key] = "missing"
            elif entry.english != plain(english):
                found[key] = "stale"
        return found

    def orphans(self, known: Sequence[str]) -> list[str]:
        """The keys that translate no text of `known` any more."""
        return sorted(set(self.entries) - set(known))


def texts(name: str, purpose: str, schema: Schema | None) -> dict[str, str]:
    """Every text of the type `name` the reference shows in both languages, by key."""
    found = {name: plain(purpose)}
    if schema is None:
        return found
    for model_name in schema.reached:
        model = schema.models[model_name]
        if model.description:
            found[f"{name}/{model.name}"] = plain(model.description)
        for item in model.fields:
            if item.description:
                found[f"{name}/{model.name}.{item.name}"] = plain(item.description)
    return found


def read_translations(text: str) -> Translations:
    """The translations a `pt-br.yml` holding `text` declares."""
    document = yaml.safe_load(text) or {}
    if not isinstance(document, dict):
        raise ValueError("the translations are one mapping of key -> en-us, pt-br")
    entries: dict[str, Translation] = {}
    for key, entry in document.items():
        if not isinstance(entry, dict) or set(entry) != {ENGLISH, PORTUGUESE}:
            raise ValueError(f"{key}: write exactly `{ENGLISH}` and `{PORTUGUESE}`")
        english, portuguese = entry[ENGLISH], entry[PORTUGUESE]
        if not isinstance(english, str) or not isinstance(portuguese, str):
            raise ValueError(f"{key}: `{ENGLISH}` and `{PORTUGUESE}` are texts")
        entries[str(key)] = Translation(plain(english), plain(portuguese))
    return Translations(entries)


def load_translations() -> Translations:
    """The translations of `docs/yaml/pt-br.yml`, none when the file is missing."""
    if not TRANSLATIONS.exists():
        return Translations()
    return read_translations(TRANSLATIONS.read_text(encoding="utf-8"))


def plain(text: str) -> str:
    """`text` with its runs of whitespace made one space, the way the page shows it."""
    return " ".join(text.split())

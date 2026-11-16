"""The generated docs pages: `docs/index.html`, the index of every doc, and
`docs/yaml-reference.html`, the YAML reference, generated from the root model of every
type in `YAML_TYPES`, from the files of each type and from the Portuguese of
`translations.yml`.

    make docs                                    # writes both pages into docs/
    python -m scripts.keiko docs --check         # exit 1 when a page is stale
    python -m scripts.keiko docs --out /tmp/ref  # writes both pages into /tmp/ref
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from scripts.keiko.yamldoc.files import Reading, read_file
from scripts.keiko.yamldoc.index import REFERENCE, index
from scripts.keiko.yamldoc.page import Documented, render
from scripts.keiko.yamldoc.schema import read_schema
from scripts.keiko.yamldoc.translations import load_translations

from app.core.commands import REPOSITORY, YAML_TYPES, YamlType

DOCS = REPOSITORY / "docs"
INDEX = "index.html"


def documented(kind: YamlType) -> Documented:
    """One YAML type with its files, its schema and what its files write."""
    files = {
        path.relative_to(REPOSITORY).as_posix(): path.read_text(encoding="utf-8")
        for path in kind.paths()
    }
    model = kind.model()
    if model is None:
        return Documented(kind, files, None, Reading())
    schema = read_schema(model)
    reading = Reading()
    for path, text in files.items():
        reading.extend(read_file(schema, text, path))
    return Documented(kind, files, schema, reading)


def reference(kinds: Sequence[YamlType] = YAML_TYPES) -> str:
    """The reference page of `kinds`, as `make docs` writes it."""
    return render([documented(kind) for kind in kinds], load_translations())


def docs_index(kinds: Sequence[YamlType] = YAML_TYPES) -> str:
    """The index of every `docs/*.md` and of the reference of `kinds`, as `make docs`
    writes it."""
    docs = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(DOCS.glob("*.md"))
    }
    return index(docs, kinds)


def pages() -> dict[str, str]:
    """Every page `make docs` writes, by its file name in `docs/`."""
    return {INDEX: docs_index(), REFERENCE: reference()}


def write(folder: Path) -> list[Path]:
    """Write every page into `folder` and return their paths."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in pages().items():
        page = folder / name
        page.write_text(text, encoding="utf-8", newline="\n")
        written.append(page)
    return written


def main(argv: Sequence[str]) -> int:
    """`python -m scripts.keiko docs [--out DIR] [--check]`."""
    parser = argparse.ArgumentParser(prog="python -m scripts.keiko docs")
    parser.add_argument("--out", type=Path, default=DOCS, help="folder of the pages")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 when a page on disk is stale"
    )
    options = parser.parse_args(list(argv))
    _offline()
    if options.check:
        return _check(options.out)
    for page in write(options.out):
        print(f"wrote {page}")
    return 0


def _check(folder: Path) -> int:
    stale = [
        folder / name
        for name, text in pages().items()
        if not (folder / name).exists()
        or (folder / name).read_text(encoding="utf-8") != text
    ]
    for page in stale:
        print(f"{page} is stale: run make docs", file=sys.stderr)
    if not stale:
        print(f"{folder} is current")
    return 1 if stale else 0


def _offline() -> None:
    """Give `app` the two globals the bot sets at start, so the models import without
    a bot, a Redis or a Mongo."""
    import app

    for name in ("bot", "redis_client"):
        if not hasattr(app, name):
            setattr(app, name, None)

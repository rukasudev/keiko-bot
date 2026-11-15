"""The YAML reference: `docs/yaml/index.html`, generated from the root model of every
type in `YAML_TYPES`, from the files of each type and from the Portuguese of
`docs/yaml/pt-br.yml`.

    make yaml-docs                                  # writes docs/yaml/index.html
    python -m scripts.keiko yamldoc --check         # exit 1 when the page is stale
    python -m scripts.keiko yamldoc --out /tmp/ref  # writes /tmp/ref/index.html
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from scripts.keiko.yamldoc.files import Reading, read_file
from scripts.keiko.yamldoc.page import Documented, render
from scripts.keiko.yamldoc.schema import read_schema
from scripts.keiko.yamldoc.translations import load_translations

from app.core.commands import REPOSITORY, YAML_TYPES, YamlType

PAGE = "index.html"
DOCS = REPOSITORY / "docs" / "yaml"


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
    """The reference page of `kinds`, as `make yaml-docs` writes it."""
    return render([documented(kind) for kind in kinds], load_translations())


def write(folder: Path) -> Path:
    """Write the reference into `folder` and return the page's path."""
    folder.mkdir(parents=True, exist_ok=True)
    page = folder / PAGE
    page.write_text(reference(), encoding="utf-8", newline="\n")
    return page


def main(argv: Sequence[str]) -> int:
    """`python -m scripts.keiko yamldoc [--out DIR] [--check]`."""
    parser = argparse.ArgumentParser(prog="python -m scripts.keiko yamldoc")
    parser.add_argument("--out", type=Path, default=DOCS, help="folder of the page")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 when the page on disk is stale"
    )
    options = parser.parse_args(list(argv))
    _offline()
    page = options.out / PAGE
    if options.check:
        current = page.read_text(encoding="utf-8") if page.exists() else ""
        if current != reference():
            print(f"{page} is stale: run make yaml-docs", file=sys.stderr)
            return 1
        print(f"{page} is current")
        return 0
    print(f"wrote {write(options.out)}")
    return 0


def _offline() -> None:
    """Give `app` the two globals the bot sets at start, so the models import without
    a bot, a Redis or a Mongo."""
    import app

    for name in ("bot", "redis_client"):
        if not hasattr(app, name):
            setattr(app, name, None)

"""Scaffold a command folder from `scripts/keiko/templates/command/`.

make command name=greet             # app/commands/greet/ and tests/commands/greet/
make command name=greet form=yes    # the same, with a form.yml the engine opens
"""

import argparse
import keyword
import re
import sys
from pathlib import Path
from string import Template
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Path(__file__).resolve().parent / "templates" / "command"
NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ScaffoldError(Exception):
    """A name the scaffold refuses, with the reason."""


def placeholders(name: str, form: bool) -> Dict[str, str]:
    """The values every template reads, for the command `name`."""
    slash = name.replace("_", "-")
    return {
        "name": name,
        "key": name,
        "slash": slash,
        "cls": "".join(part.capitalize() for part in name.split("_")),
    }


def plan(name: str, form: bool) -> List[Tuple[str, str]]:
    """Each file to write, as (template, path relative to the repository)."""
    variant = "form" if form else "plain"
    folder = f"app/commands/{name}"
    files = [
        ("__init__.py.tmpl", f"{folder}/__init__.py"),
        ("cog.py.tmpl", f"{folder}/cog.py"),
        (f"service.{variant}.py.tmpl", f"{folder}/service.py"),
        (f"copy.{variant}.yml.tmpl", f"{folder}/copy.yml"),
        (f"test.{variant}.py.tmpl", f"tests/commands/{name}/test_{name}.py"),
    ]
    if form:
        files.insert(3, ("form.yml.tmpl", f"{folder}/form.yml"))
    return files


def check(name: str, root: Path) -> None:
    """Refuse a name that is no folder name, or a command that already exists."""
    if not NAME.match(name) or keyword.iskeyword(name):
        raise ScaffoldError(
            f"{name!r} is not a command folder name: lower case, digits and _"
        )
    for folder in (
        root / "app" / "commands" / name,
        root / "tests" / "commands" / name,
    ):
        if folder.exists():
            raise ScaffoldError(f"{folder.relative_to(root)} already exists")


def scaffold(name: str, form: bool = False, root: Path = ROOT) -> List[Path]:
    """Write the folder of the command `name` and its test; return what was written."""
    check(name, root)
    values = placeholders(name, form)
    written = []

    for template, target in plan(name, form):
        text = Template((TEMPLATES / template).read_text(encoding="utf-8"))
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.substitute(values), encoding="utf-8")
        written.append(path)

    for package in (root / "tests" / "commands", root / "tests" / "commands" / name):
        marker = package / "__init__.py"
        if not marker.exists():
            marker.write_text("", encoding="utf-8")
            written.append(marker)
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="make command name=<x> [form=yes]")
    parser.add_argument("name", nargs="?", default="", help="the command folder")
    parser.add_argument("--form", action="store_true", help="add a form.yml")
    arguments = parser.parse_args(argv)

    try:
        written = scaffold(arguments.name, arguments.form)
    except ScaffoldError as error:
        print(f"make command: {error}", file=sys.stderr)
        return 1

    for path in written:
        print(f"created {path.relative_to(ROOT)}")
    print(
        f"next: add `{arguments.name}` to COMMANDS in app/commands/__init__.py, "
        "write the copy, then run the tests (docs/commands.md)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

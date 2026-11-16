"""Every YAML file is laid out by its schema: a union item writes its kind first, then
the closed keys in schema order, then the open blocks, each under one `# open:` line;
a model `in_order` writes every key in schema order.

The files not converted yet are listed in `EXCEPTIONS` by type; a listed file that
already follows the standard fails, so the list only shrinks. A type without a schema
yet (`schema=""` in `YAML_TYPES`) is not checked; its files stay listed until it gets
one.
"""

import os
import sys

import pytest

from tests.helpers.yaml_schemas import FLOW, GLOSSARY, Flow, Glossary

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.keiko.yamldoc.files import read_file  # noqa: E402
from scripts.keiko.yamldoc.schema import read_schema  # noqa: E402

from app.core.commands import REPOSITORY, YAML_TYPES  # noqa: E402
from app.core.copy_schema import CommandCopy  # noqa: E402

pytestmark = pytest.mark.unit

EXCEPTIONS: dict[str, frozenset[str]] = {}


def _checked():
    for kind in YAML_TYPES:
        model = kind.model()
        if model is None:
            continue
        schema = read_schema(model)
        for path in kind.paths():
            yield pytest.param(
                kind.name, schema, path, id=path.relative_to(REPOSITORY).as_posix()
            )


@pytest.mark.parametrize("name, schema, path", list(_checked()))
def test_every_file_is_laid_out_by_its_schema(name, schema, path):
    relative = path.relative_to(REPOSITORY).as_posix()
    problems = read_file(schema, path.read_text(encoding="utf-8"), relative).problems
    listed = relative in EXCEPTIONS.get(name, frozenset())

    if listed:
        assert problems, (
            f"{relative} follows the layout standard now: remove it from EXCEPTIONS "
            f"in tests/test_yaml_layout.py (under {name!r})"
        )
    else:
        assert not problems, (
            f"{relative} breaks the layout standard (closed keys in schema order, "
            "then the open blocks under one `# open:` line):\n"
            + "\n".join(str(problem) for problem in problems)
        )


def test_the_exceptions_name_registered_files_only():
    registered = {
        kind.name: {path.relative_to(REPOSITORY).as_posix() for path in kind.paths()}
        for kind in YAML_TYPES
    }
    stale = sorted(
        f"{name}: {path}"
        for name, paths in EXCEPTIONS.items()
        for path in paths
        if path not in registered.get(name, set())
    )

    assert not stale, f"EXCEPTIONS lists files no YAML type has: {stale}"


def test_a_file_laid_out_by_the_standard_passes():
    assert read_file(read_schema(Flow), FLOW, "flow.yml").problems == []
    assert read_file(read_schema(Glossary), GLOSSARY, "glossary.yml").problems == []


@pytest.mark.parametrize(
    "broken, says",
    [
        (
            FLOW.replace(
                "  - kind: pick\n    key: color", "  - key: color\n    kind: pick"
            ),
            "the standard order is kind, key, title, style, options",
        ),
        (
            FLOW.replace("    # open: text names -> Words\n", ""),
            "the open block `texts`",
        ),
        (
            FLOW.replace("version: 2\n", "").replace(
                "  color: The color\n", "  color: The color\nversion: 2\n"
            ),
            "Flow writes steps, names, version",
        ),
        (
            FLOW.replace("    style: select", "    shape: select"),
            "`shape` is no key of PickStep",
        ),
        (
            FLOW.replace("  - kind: pick\n    key: color", "  - key: color"),
            "no `kind`: a Step names one of intro, pick",
        ),
        (FLOW.replace("kind: pick", "kind: choice"), "`kind: choice` is none of"),
    ],
    ids=[
        "kind-not-first",
        "open-without-comment",
        "open-before-closed",
        "unknown-key",
        "no-kind",
        "unknown-kind",
    ],
)
def test_a_broken_layout_says_where_and_how_to_fix_it(broken, says):
    problems = read_file(read_schema(Flow), broken, "flow.yml").problems

    assert any(says in str(problem) for problem in problems), [str(p) for p in problems]


def test_an_open_file_needs_its_comment_line():
    bare = GLOSSARY.replace("# open: text names -> Words\n", "")

    problems = read_file(read_schema(Glossary), bare, "glossary.yml").problems

    assert [problem.line for problem in problems] == [1]
    assert "the first key of this open file" in problems[0].message



FACTS = """\
# open: fact names -> a fact the code passes
facts:
  seen: {type: boolean, description: Whether it was seen.}
"""
SLASH = "  slash:\n    name: {en-us: sample, pt-br: amostra}\n"
SETUP = (
    "  setup:\n    label: {en-us: Sample, pt-br: Amostra}\n"
    "    description: {en-us: A sample, pt-br: Uma amostra}\n"
)
TEXTS = "  # open: text names -> a text\n  texts:\n    hint: {en-us: Hint, pt-br: Dica}\n"
ANNOUNCEMENT = (
    "  announcement:\n    defaults:\n      title: {en-us: Hi, pt-br: Oi}\n"
    "      description: {en-us: Hello, pt-br: Olá}\n"
)


def _copy(*parts: str) -> str:
    return "".join(parts)


def test_a_copy_writes_its_facts_then_its_fixed_places_then_what_it_draws():
    text = _copy(FACTS, "command:\n", SLASH, SETUP, "ui:\n", TEXTS, ANNOUNCEMENT)

    assert read_file(read_schema(CommandCopy), text, "copy.yml").problems == []


@pytest.mark.parametrize(
    "broken, says",
    [
        (
            _copy("command:\n", SLASH, SETUP, FACTS, "ui:\n", TEXTS, ANNOUNCEMENT),
            "the standard order is facts, command, ui (every key in schema order)",
        ),
        (
            _copy(FACTS, "command:\n", SETUP, SLASH, "ui:\n", TEXTS, ANNOUNCEMENT),
            "the standard order is slash, setup",
        ),
        (
            _copy(FACTS, "command:\n", SLASH, SETUP, "ui:\n", ANNOUNCEMENT, TEXTS),
            "the standard order is texts, announcement",
        ),
    ],
    ids=["facts-not-first", "setup-before-slash", "announcement-first"],
)
def test_a_copy_out_of_its_order_says_the_order(broken, says):
    problems = read_file(read_schema(CommandCopy), broken, "copy.yml").problems

    assert any(says in str(problem) for problem in problems), [str(p) for p in problems]

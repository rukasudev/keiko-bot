"""Every fact the code passes is declared, read from the syntax tree of `app/`.

The names of each `facts(owner, ...)`, each command's `command_facts(...)` and each
`fill(...)` are facts of their owner (the command whose folder the call is in, or
`global` in a shared layer) or of the global catalog; and a primitive called on a
literal block (`response_embed`, `response_error_embed`, `card_copy`,
`block_texts`, `RecordsBrowser(copy=)`, `panel_info_copy`) is passed every fact the
block reads, so a forgotten fact fails here and not in the one state that draws it.
"""

import ast
import os
from dataclasses import dataclass
from importlib import import_module
from typing import Iterator, List, Optional, Set

import pytest
from pydantic import BaseModel

from app.core import i18n
from app.core.commands import GLOBAL
from app.core.facts import address, global_catalog, owner_of

pytestmark = pytest.mark.unit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS = "app/commands/"
BUILDERS = {"facts", "command_facts"}
KEYED = {
    "response_embed": 0,
    "response_error_embed": 0,
    "card_copy": 0,
    "block_texts": 0,
}
FACTS_AT = {"card_copy": 2, "block_texts": 1, "panel_info_copy": 1}


@dataclass(frozen=True)
class Problem:
    """A call that passes a fact no catalog declares, or misses one its block reads."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _owner(path: str) -> str:
    folder, separator, _file = path[len(COMMANDS) :].partition("/")
    if not path.startswith(COMMANDS) or not separator:
        return GLOBAL
    return import_module(f"app.commands.{folder}").COMMAND.key


def _name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _literal(node: Optional[ast.expr]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id == "GLOBAL":
        return GLOBAL
    return None


def _declared(owner: str) -> Set[str]:
    return {*i18n.fact_specs(owner), *global_catalog().facts}


def _keywords(call: ast.Call) -> List[str]:
    return [keyword.arg for keyword in call.keywords if keyword.arg]


def _built_by(call: ast.Call, owner: str) -> Optional[str]:
    """The owner a `facts(...)` or `command_facts(...)` call addresses its names to."""
    name = _name(call)
    if name == "command_facts":
        return owner
    if name == "facts":
        return _literal(call.args[0] if call.args else None)
    return None


def _passed(call: ast.Call, owner: str) -> Optional[Set[str]]:
    """The addresses a primitive call passes, None when the code builds them."""
    found = next((kw.value for kw in call.keywords if kw.arg == "facts"), None)
    position = FACTS_AT.get(_name(call))
    if found is None and position is not None and len(call.args) > position:
        found = call.args[position]
    if found is None:
        return set()
    if isinstance(found, ast.Dict) and all(key is not None for key in found.keys):
        keys = [_literal(key) for key in found.keys]
        return None if None in keys else {key for key in keys if key}
    if isinstance(found, ast.Call) and _name(found) in BUILDERS:
        built = _built_by(found, owner)
        if built is None or None in [kw.arg for kw in found.keywords]:
            return None
        return {address(built, name) for name in _keywords(found)}
    return None


def _key(call: ast.Call, owner: str) -> Optional[str]:
    name = _name(call)
    if name == "RecordsBrowser":
        return _literal(
            next((kw.value for kw in call.keywords if kw.arg == "copy"), None)
        )
    if name == "panel_info_copy":
        return f"{owner}.command.panel.info" if owner != GLOBAL else None
    if name not in KEYED or len(call.args) <= KEYED[name]:
        return None
    key = _literal(call.args[KEYED[name]])
    if key and name == "response_error_embed" and "." not in key:
        return f"errors.{key}"
    return key


def problems(source: str, path: str) -> Iterator[Problem]:
    """Every call of `source` that names an undeclared fact or misses a fact its
    literal block reads."""
    owner = _owner(path)
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _name(node)
        built = _built_by(node, owner) if name in BUILDERS else None
        named = built or (owner if name == "fill" else None)
        if named:
            declared = _declared(named)
            for fact in _keywords(node):
                if fact not in declared:
                    yield Problem(
                        path, node.lineno, f"{name}(): ${fact} is no fact of {named}"
                    )
        key = _key(node, owner)
        passed = _passed(node, owner) if key else None
        if key and passed is not None:
            block = i18n.block(key, BaseModel)
            missing = sorted(set(i18n.needs(block, owner_of(key))) - passed)
            if missing:
                yield Problem(
                    path,
                    node.lineno,
                    f"{name}({key!r}) reads {', '.join(missing)}, which it does not pass",
                )


def _sources() -> Iterator[tuple[str, str]]:
    for folder, subfolders, filenames in os.walk(os.path.join(ROOT, "app")):
        subfolders[:] = sorted(name for name in subfolders if name != "__pycache__")
        for filename in sorted(filenames):
            if filename.endswith(".py") and not filename.endswith("_test.py"):
                full = os.path.join(folder, filename)
                relative = os.path.relpath(full, ROOT).replace(os.sep, "/")
                with open(full, encoding="utf-8") as handle:
                    yield handle.read(), relative


def test_every_fact_the_code_passes_is_declared_and_every_block_gets_its_own():
    found = [
        str(problem)
        for source, path in _sources()
        for problem in problems(source, path)
    ]

    assert found == [], "\n".join(found)


@pytest.mark.parametrize(
    "source, says",
    [
        (
            "command_facts(would_blok=True)",
            "command_facts(): $would_blok is no fact of block_links",
        ),
        ("facts(GLOBAL, removng='one')", "facts(): $removng is no fact of global"),
        ("fill(line, mod='x')", "fill(): $mod is no fact of block_links"),
        (
            "response_embed('block_links.ui.responses.check', locale, "
            "facts=command_facts(would_block=True, mode='x'))",
            "reads global.facts.feature_state, which it does not pass",
        ),
        (
            "card_copy('setup.ui.cards.dashboard', locale)",
            "reads setup.facts.all_configured, which it does not pass",
        ),
    ],
    ids=["command fact", "global fact", "fill", "a forgotten fact", "no facts at all"],
)
def test_a_wrong_call_says_where_and_what(source, says):
    path = "app/commands/block_links/service.py"

    found = [str(problem) for problem in problems(source, path)]

    assert any(says in line for line in found), found

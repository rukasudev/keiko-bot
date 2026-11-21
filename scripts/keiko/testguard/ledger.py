"""The ledger of past bugs: every "Broke as" docstring and the test pinning it now."""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import yaml

LEDGER = Path(__file__).with_name("ledger.yml")

MARK = "Broke as"

ENDS = (
    "Shared behaviour",
    "Shared behavior",
    "Guaranteed",
    "Consumer",
    "Exposed by",
    "What must remain",
)

HEADER = """\
# Every past bug the suite pins: one entry per "Broke as" docstring found on
# the day it was recorded. `test` is the test that pins it now (`path::name`,
# `path::Class::name`, or `path` when the module docstring tells the bug). A
# rebuild that moves or merges a test points the entry at its new home; an
# entry is never deleted. `python -m scripts.keiko testguard ledger` fails
# when an entry names a test that does not exist.
"""


@dataclass(frozen=True)
class Pin:
    """One "Broke as" paragraph and the test whose docstring holds it."""

    test: str
    broke: str
    guarantee: str


@dataclass
class Check:
    """What the ledger check found: entries without their test and new bugs."""

    entries: int
    missing: List[Dict[str, str]] = field(default_factory=list)
    history_dropped: List[str] = field(default_factory=list)
    unledgered: List[Pin] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every entry still has its test."""
        return not self.missing


def test_files(root: Path) -> List[Path]:
    """Every file pytest collects tests from, in path order."""
    found = set(root.glob("tests/**/test_*.py"))
    found.update(root.glob("tests/**/*_test.py"))
    found.update(root.glob("app/**/*_test.py"))
    return sorted(path for path in found if "__pycache__" not in path.parts)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _short(text: str, limit: int) -> str:
    text = _flat(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _broke(segment: str) -> str:
    body = segment[len(MARK) :]
    cut = len(body)
    for end in (*ENDS, "\n\n"):
        index = body.find(end)
        if 0 <= index < cut:
            cut = index
    return _short(body[:cut].lstrip(" :"), 300)


def _guarantee(docstring: str, segment: str, name: str) -> str:
    match = re.search(r"Guaranteed[^:]*:", segment)
    if match:
        rest = segment[match.end() :]
        return _short(rest.split("\n\n", 1)[0], 400)
    summary = docstring.strip().split("\n\n", 1)[0]
    if summary and not summary.startswith(MARK):
        return _short(summary, 400)
    return name.split("::")[-1].removeprefix("test_").replace("_", " ")


def _pins(docstring: Optional[str], pointer: str) -> Iterator[Pin]:
    if not docstring or MARK not in docstring:
        return
    starts = [match.start() for match in re.finditer(re.escape(MARK), docstring)]
    for number, start in enumerate(starts):
        stop = starts[number + 1] if number + 1 < len(starts) else len(docstring)
        segment = docstring[start:stop]
        yield Pin(
            test=pointer,
            broke=_broke(segment),
            guarantee=_guarantee(docstring, segment, pointer),
        )


def _tests(tree: ast.Module, path: str) -> Iterator[Tuple[str, ast.AST]]:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                yield f"{path}::{node.name}", node
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if member.name.startswith("test"):
                        yield f"{path}::{node.name}::{member.name}", member


def _parsed(root: Path) -> Iterator[Tuple[str, ast.Module]]:
    for file in test_files(root):
        yield str(file.relative_to(root)), ast.parse(file.read_text(encoding="utf-8"))


def scan(root: Path) -> List[Pin]:
    """Every "Broke as" paragraph of the suite, in module and test docstrings."""
    found: List[Pin] = []
    for path, tree in _parsed(root):
        found.extend(_pins(ast.get_docstring(tree, clean=False), path))
        for pointer, node in _tests(tree, path):
            found.extend(_pins(ast.get_docstring(node, clean=False), pointer))
    return found


def defined(root: Path) -> Dict[str, bool]:
    """Every pointer an entry may name, and whether its docstring still tells a bug."""
    pointers: Dict[str, bool] = {}
    for path, tree in _parsed(root):
        tests = list(_tests(tree, path))
        if tests:
            module = ast.get_docstring(tree, clean=False) or ""
            pointers[path] = MARK in module
        for pointer, node in tests:
            pointers[pointer] = MARK in (ast.get_docstring(node, clean=False) or "")
    return pointers


def load(path: Path = LEDGER) -> List[Dict[str, str]]:
    """The ledger's entries, in id order."""
    if not path.exists():
        return []
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(document.get("entries") or [])


def write(entries: Sequence[Dict[str, str]], path: Path = LEDGER) -> None:
    """Write the ledger with its header, one entry per past bug."""
    body = yaml.safe_dump(
        {"entries": list(entries)}, allow_unicode=True, sort_keys=False, width=88
    )
    path.write_text(HEADER + body, encoding="utf-8")


def check(root: Path, entries: Sequence[Dict[str, str]]) -> Check:
    """Entries whose test is gone, tests that lost their history, and bugs not in it."""
    pointers = defined(root)
    result = Check(entries=len(entries))
    held: Dict[str, int] = {}
    for entry in entries:
        pointer = entry["test"]
        held[pointer] = held.get(pointer, 0) + 1
        if pointer not in pointers:
            result.missing.append(entry)
        elif not pointers[pointer]:
            result.history_dropped.append(entry["id"])
    seen: Dict[str, int] = {}
    for pin in scan(root):
        seen[pin.test] = seen.get(pin.test, 0) + 1
        if seen[pin.test] > held.get(pin.test, 0):
            result.unledgered.append(pin)
    return result


def sync(root: Path, path: Path = LEDGER) -> List[Dict[str, str]]:
    """Add every unledgered "Broke as" paragraph to the ledger; return what it added."""
    entries = load(path)
    numbers = [int(entry["id"].split("-")[1]) for entry in entries]
    following = max(numbers, default=0) + 1
    added = []
    for pin in check(root, entries).unledgered:
        added.append(
            {
                "id": f"pb-{following:03d}",
                "test": pin.test,
                "guarantee": pin.guarantee,
                "broke": pin.broke,
            }
        )
        following += 1
    if added:
        write([*entries, *added], path)
    return added


def _instance_of(pointer: str, nodeid: str) -> bool:
    if "::" not in pointer:
        return nodeid.startswith(f"{pointer}::")
    return nodeid == pointer or nodeid.startswith(f"{pointer}[")


def outcome(entry: Dict[str, str], results: Dict[str, Dict[str, object]]) -> str:
    """How the entry's test ended in a run: passed, failed, skipped or not run."""
    found = [
        str(result["outcome"])
        for nodeid, result in results.items()
        if _instance_of(entry["test"], nodeid)
    ]
    if not found:
        return "not run"
    if any(item in ("failed", "error") for item in found):
        return "failed"
    if all(item == "skipped" for item in found):
        return "skipped"
    return "passed"

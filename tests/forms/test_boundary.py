"""The lines the form platform never crosses.

`app/forms/` imports `discord` only under `adapters/discord/`, the platform
(definitions, engine, kinds, extensions) imports nothing else from `app`
except `app.forms` and `app.constants`, no module names a command key, no
module blocks the event loop, and no comment tells a story.
"""

import os
import re

import pytest

pytestmark = pytest.mark.unit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FORMS = os.path.join(ROOT, "app", "forms")
ADAPTER = os.path.join("app", "forms", "adapters", "discord")
PLATFORM = tuple(
    os.path.join("app", "forms", part)
    for part in ("definitions", "engine", "kinds", "extensions")
)

IMPORTS_DISCORD = re.compile(r"^\s*(?:from|import)\s+discord\b", re.MULTILINE)
IMPORTS_APP = re.compile(r"^\s*(?:from|import)\s+(app(?:\.[\w.]+)?)\b", re.MULTILINE)
BLOCKING = re.compile(
    r"^\s*(?:from|import)\s+(?:requests|pymongo)\b|\btime\.sleep\(", re.MULTILINE
)
COMMAND_KEY = re.compile(r"\bcommand_key\b")
REFLECTION = re.compile(r"\b(?:hasattr|getattr)\(")
COMMENT_BLOCK = re.compile(r"^[ \t]*#(?!!).*\n(?:[ \t]*#.*\n)+", re.MULTILINE)


def python_files(directory):
    for folder, _subfolders, filenames in os.walk(directory):
        if "__pycache__" in folder:
            continue
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield os.path.join(folder, filename)


def _relative(path):
    return os.path.relpath(path, ROOT)


def _source(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _offenders(pattern, paths):
    return [_relative(path) for path in paths if pattern.search(_source(path))]


def _platform_files():
    return [
        path for path in python_files(FORMS) if _relative(path).startswith(PLATFORM)
    ]


def _outside_adapter():
    return [path for path in python_files(FORMS) if ADAPTER not in _relative(path)]


def test_only_the_discord_adapter_imports_discord():
    assert _offenders(IMPORTS_DISCORD, _outside_adapter()) == []


def test_the_platform_imports_nothing_internal_but_itself_and_constants():
    offenders = []
    for path in _platform_files():
        for module in IMPORTS_APP.findall(_source(path)):
            if not module.startswith(("app.forms", "app.constants")):
                offenders.append((_relative(path), module))
    assert offenders == []


def test_the_platform_never_names_a_command_key():
    assert _offenders(COMMAND_KEY, _platform_files()) == []


def test_nothing_in_the_platform_blocks_the_event_loop():
    assert _offenders(BLOCKING, list(python_files(FORMS))) == []


def test_no_reflection_outside_the_adapter():
    assert _offenders(REFLECTION, _outside_adapter()) == []


def test_no_comment_block_longer_than_one_line():
    assert _offenders(COMMENT_BLOCK, list(python_files(FORMS))) == []

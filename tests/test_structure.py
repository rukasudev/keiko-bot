"""The import contracts of the command tree, read from the syntax tree of `app/`.

A command folder imports the shared layers and never another command. The shared
layers never import a command; the registry alone reads the list, inside a function.
The form engine imports `discord` only in its adapter, and the data layer imports
nothing of the app but itself and the constants. Every import counts, lazy ones and
`import_module("...")` included; co-located `*_test.py` files are tests, not layers.
A place that may still cross a line is listed in EXCEPTIONS with its reason, and a
listed place that no longer crosses it fails as stale.
"""

import ast
import os
from dataclasses import dataclass
from typing import Dict, Iterator, List, Tuple

import pytest

pytestmark = pytest.mark.unit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS = "app/commands"
SHARED_LAYERS = ("app/core", "app/forms", "app/ui", "app/integrations", "app/data")
FORMS = "app/forms"
FORMS_ADAPTER = "app/forms/discord"
DATA = "app/data"
NOT_MOVED = (
    "app/commands does not exist yet: the import contracts start with the move "
    "into the new tree"
)
moved = pytest.mark.skipif(
    not os.path.isdir(os.path.join(ROOT, COMMANDS)), reason=NOT_MOVED
)

COMMAND_READS: Dict[str, str] = {
    "app": "the package root: the running bot and the clients create_app builds",
    "app.core": "the shared modules and the registry",
    "app.forms": "the form engine and its Discord adapter",
    "app.ui": "the generic Discord views and components",
    "app.integrations": "the clients of the outside world",
    "app.data": "the repositories",
    "app.constants": "the shared limits and tunables",
    "app.config": "the settings read from the environment",
    "app.logger": "the log calls",
    "app.bot": "the DiscordBot a cog receives",
    "app.decorators": "keiko_command and the permission checks of a slash command",
    "app.translator": "locale_str, the translated name of a slash command",
    "app.types": "the Cog and Group bases",
    "app.exceptions": "the errors a service raises and the error handler reads",
}
DATA_READS = ("app.data", "app.constants")
LAZY_READS: Dict[Tuple[str, str], str] = {
    ("app/core/commands.py", "app.commands"): (
        "the registry reads the ordered list when first asked, so a command can "
        "import core"
    ),
}
EXCEPTIONS: Dict[Tuple[str, str], str] = {}


@dataclass(frozen=True)
class Import:
    """One module a file imports, and whether it is imported inside a function."""

    path: str
    line: int
    module: str
    lazy: bool


def _python_files(root: str, layer: str) -> Iterator[str]:
    for folder, subfolders, filenames in os.walk(os.path.join(root, layer)):
        subfolders[:] = sorted(name for name in subfolders if name != "__pycache__")
        for filename in sorted(filenames):
            if filename.endswith(".py") and not filename.endswith("_test.py"):
                full = os.path.join(folder, filename)
                yield os.path.relpath(full, root).replace(os.sep, "/")


def _is_module(root: str, dotted: str) -> bool:
    base = os.path.join(root, *dotted.split("."))
    return os.path.isfile(f"{base}.py") or os.path.isfile(
        os.path.join(base, "__init__.py")
    )


def _dynamic_module(node: ast.Call) -> str:
    name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
    if isinstance(node.func, ast.Name):
        name = node.func.id
    if name != "import_module" or not node.args:
        return ""
    argument = node.args[0]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    if isinstance(argument, ast.JoinedStr) and argument.values:
        head = argument.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value.rstrip(".")
    return ""


def _modules(node: ast.AST, path: str, root: str) -> List[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.Call):
        return [module for module in [_dynamic_module(node)] if module]
    if not isinstance(node, ast.ImportFrom):
        return []
    package = path[: -len(".py")].split("/")[:-1]
    base = package[: len(package) - node.level + 1] if node.level else []
    module = ".".join([*base, node.module] if node.module else base)
    submodules = [f"{module}.{alias.name}" for alias in node.names]
    return [module, *(name for name in submodules if _is_module(root, name))]


def imports(path: str, root: str = ROOT) -> List[Import]:
    """Every module `path` imports, with its line and whether it is lazy."""
    with open(os.path.join(root, path), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found: List[Import] = []

    def visit(node: ast.AST, lazy: bool) -> None:
        for child in ast.iter_child_nodes(node):
            for module in _modules(child, path, root):
                found.append(Import(path, child.lineno, module, lazy))
            inner = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            visit(child, lazy or inner)

    visit(tree, False)
    return found


def _within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _command_of(path: str) -> str:
    parts = path.split("/")
    return parts[2] if len(parts) > 3 and path.startswith(f"{COMMANDS}/") else ""


def command_crossings(root: str = ROOT) -> List[Import]:
    """Imports of a command folder that reach another command or an unlisted module."""
    found = []
    for path in _python_files(root, COMMANDS):
        own = _command_of(path)
        if not own:
            continue
        for found_import in imports(path, root):
            module = found_import.module
            if _within(module, "app.commands"):
                allowed = _within(module, f"app.commands.{own}")
            elif _within(module, "app"):
                allowed = module == "app" or any(
                    _within(module, read) for read in COMMAND_READS if read != "app"
                )
            else:
                allowed = True
            if not allowed:
                found.append(found_import)
    return found


def shared_crossings(root: str = ROOT) -> List[Import]:
    """Imports of a shared layer that reach `app.commands`, the lazy registry aside."""
    found = []
    for layer in SHARED_LAYERS:
        for path in _python_files(root, layer):
            for found_import in imports(path, root):
                if not _within(found_import.module, "app.commands"):
                    continue
                lazy_read = (path, found_import.module) in LAZY_READS
                if not (lazy_read and found_import.lazy):
                    found.append(found_import)
    return found


def discord_crossings(root: str = ROOT) -> List[Import]:
    """Imports of `discord` in the form engine outside its adapter."""
    return [
        found_import
        for path in _python_files(root, FORMS)
        if not path.startswith(f"{FORMS_ADAPTER}/")
        for found_import in imports(path, root)
        if _within(found_import.module, "discord")
    ]


def data_crossings(root: str = ROOT) -> List[Import]:
    """Imports of the data layer that reach the app outside itself and the constants."""
    return [
        found_import
        for path in _python_files(root, DATA)
        for found_import in imports(path, root)
        if _within(found_import.module, "app")
        and not any(_within(found_import.module, read) for read in DATA_READS)
    ]


def _unlisted(crossings: List[Import]) -> List[str]:
    return [
        f"{found.path}:{found.line} {found.module}"
        for found in crossings
        if (found.path, found.module) not in EXCEPTIONS
    ]


def _every_crossing(root: str = ROOT) -> List[Import]:
    return [
        *command_crossings(root),
        *shared_crossings(root),
        *discord_crossings(root),
        *data_crossings(root),
    ]


@moved
def test_every_layer_the_contracts_name_exists():
    layers = (COMMANDS, *SHARED_LAYERS, FORMS_ADAPTER)
    missing = [
        layer for layer in layers if not os.path.isdir(os.path.join(ROOT, layer))
    ]
    assert missing == [], "a layer moved: point the contracts at its new path"


@moved
def test_a_command_imports_the_shared_layers_and_never_another_command():
    unlisted = _unlisted(command_crossings())
    assert unlisted == [], "\n".join(
        [
            "a command reads core, forms, ui, integrations, data and COMMAND_READS; "
            "what two commands share moves to a shared layer",
            *unlisted,
        ]
    )


@moved
def test_the_shared_layers_never_import_a_command():
    unlisted = _unlisted(shared_crossings())
    assert unlisted == [], "\n".join(
        [
            "a shared layer takes the command as a value from the registry "
            "(app.core.commands), never by import",
            *unlisted,
        ]
    )


@moved
def test_the_form_engine_imports_discord_only_in_its_adapter():
    unlisted = _unlisted(discord_crossings())
    assert unlisted == [], "\n".join(
        [f"only {FORMS_ADAPTER} imports discord", *unlisted]
    )


def test_the_data_layer_imports_only_itself_and_the_constants():
    unlisted = _unlisted(data_crossings())
    assert unlisted == [], "\n".join(
        ["app/data imports only app.data and app.constants", *unlisted]
    )


def test_every_exception_still_matches():
    crossing = {(found.path, found.module) for found in _every_crossing()}
    stale = sorted(
        f"{path} {module} ({reason})"
        for (path, module), reason in EXCEPTIONS.items()
        if (path, module) not in crossing
    )
    assert stale == [], "\n".join(
        ["these no longer cross a line: drop them from EXCEPTIONS", *stale]
    )


def test_the_scan_sees_each_contract(tmp_path):
    """Relative, lazy and dynamic imports; the lazy registry read is the one allowed."""
    files = {
        "app/commands/__init__.py": "from app.commands.birthdays import COMMAND\n",
        "app/commands/birthdays/__init__.py": "from . import service\n",
        "app/commands/birthdays/service.py": (
            "from app.core import cache\n"
            "from app import bot\n"
            "from app.services import setup\n"
            "def welcome():\n"
            "    from app.commands.welcome import service\n"
        ),
        "app/commands/welcome/__init__.py": "",
        "app/commands/welcome/service.py": (
            "from importlib import import_module\n"
            'import_module(f"app.commands.{name}.feature")\n'
        ),
        "app/core/commands.py": (
            "def listed():\n"
            "    from app.commands import COMMANDS\n"
            "    return COMMANDS\n"
        ),
        "app/core/trace.py": "from app.commands import COMMANDS\n",
        "app/forms/engine.py": "import discord\n",
        "app/forms/discord/adapter.py": "from discord import ui\n",
        "app/forms/discord_text.py": "",
        "app/data/guild.py": (
            "from app.data import collection\nfrom app import logger\n"
        ),
        "app/data/collection.py": "",
        "app/logger.py": "",
    }
    for path, source in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)
    root = str(tmp_path)

    def seen(crossings):
        return [(found.path, found.line, found.module) for found in crossings]

    assert seen(command_crossings(root)) == [
        ("app/commands/birthdays/service.py", 3, "app.services"),
        ("app/commands/birthdays/service.py", 5, "app.commands.welcome"),
        ("app/commands/birthdays/service.py", 5, "app.commands.welcome.service"),
        ("app/commands/welcome/service.py", 2, "app.commands"),
    ]
    assert seen(shared_crossings(root)) == [("app/core/trace.py", 1, "app.commands")]
    assert seen(discord_crossings(root)) == [("app/forms/engine.py", 1, "discord")]
    assert seen(data_crossings(root)) == [
        ("app/data/guild.py", 2, "app"),
        ("app/data/guild.py", 2, "app.logger"),
    ]

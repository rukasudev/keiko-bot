"""The static contracts of the command tree, read from the syntax tree of `app/`.

Imports. A command folder imports the shared layers and never another command. The
shared layers never import a command; the registry alone reads the list, inside a
function. The form engine imports `discord` only in its adapter, and the data layer
imports nothing of the app but itself and the constants. Every import counts, lazy ones
and `import_module("...")` included; co-located `*_test.py` files are tests, not layers.
A place that may still cross a line is listed in EXCEPTIONS with its reason, and a
listed place that no longer crosses it fails as stale.

Names (code-style rule 22). A module of the generic layers holds no command key, no
constant that holds one, no feature word in a name or a string literal, and no import
of a command's module; the places that still do are listed in NAMING_EXCEPTIONS until
the design-pattern pull request moves them, and a listed place that no longer matches
fails as stale. Comments and docstrings are prose and do not count. A command key is
every key the registry declares except the platform words help, setup, report and
support, which also name the form's help aside, its setup mode, a listener step and
the API's route to the support server (their modules are refused as imports). A
feature word is matched inside snake case, camel case and hyphenated text alike;
`greeting_sent` is the server's own record that Keiko greeted it, not the greeting.
"""

import ast
import functools
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterator, List, Set, Tuple

import pytest

from app.constants import Commands
from app.core.commands import commands

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


GENERIC_LAYERS = (
    "app/data/analytics",
    "app/data/events",
    "app/data/guild",
    "app/data/keiko",
    "app/data/traces",
    "app/data/util.py",
    "app/core",
    "app/ui",
    "app/forms/form",
    "app/forms/discord",
    "app/forms/features/__init__.py",
    "app/forms/features/feature.py",
    "app/forms/features/subscriptions.py",
    "app/integrations/reminders/webhook.py",
    "app/integrations/http.py",
    "app/migrations/runner.py",
    "app/migrations/helpers.py",
    "app/migrations/__main__.py",
)
PLATFORM_WORDS = frozenset({"help", "setup", "report", "support"})
COMMAND_KEYS = frozenset(command.key for command in commands()) - PLATFORM_WORDS
KEY_CONSTANTS = frozenset(
    {
        name
        for name, value in vars(Commands).items()
        if isinstance(value, str) and value in COMMAND_KEYS
    }
)
FEATURE_WORDS = (
    "birthday",
    "twitch",
    "streamer",
    "youtube",
    "stream_elements",
    "welcome",
    "block_links",
    "blocked_link",
    "default_roles",
    "greeting",
    "inspection",
)
COMMAND_MODULE = re.compile(r"app\.commands\.\w+")
CAMEL_HUMP = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
SEPARATOR = re.compile(r"[\s\-]+")
NOT_A_FEATURE = re.compile(r"greeting_sent")
RULE = (
    "rule 22: take the feature key as a parameter, or move this to the command's module"
)
DESIGN_PATTERN_PR = "design-pattern PR"

Finding = Tuple[str, int, str]


def _listed(path: str, *tokens: str) -> Dict[Tuple[str, str], str]:
    return {(path, token): DESIGN_PATTERN_PR for token in tokens}


NAMING_EXCEPTIONS: Dict[Tuple[str, str], str] = {
}


def _generic_files() -> Iterator[str]:
    for layer in GENERIC_LAYERS:
        path = os.path.join(ROOT, layer)
        if os.path.isfile(path):
            yield layer
            continue
        for folder, _subfolders, filenames in os.walk(path):
            for filename in sorted(filenames):
                if filename.endswith(".py") and not filename.endswith("_test.py"):
                    full = os.path.join(folder, filename)
                    yield os.path.relpath(full, ROOT).replace(os.sep, "/")


def _docstrings(tree: ast.AST) -> Set[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, owners) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _feature_word(text: str) -> str:
    plain = SEPARATOR.sub("_", CAMEL_HUMP.sub("_", text)).lower()
    plain = NOT_A_FEATURE.sub("", plain)
    return next((word for word in FEATURE_WORDS if word in plain), "")


def _imported_modules(node: ast.AST, path: str, root: str) -> List[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    package = path[: -len(".py")].split("/")[:-1]
    base = package[: len(package) - node.level + 1] if node.level else []
    module = ".".join([*base, node.module] if node.module else base)
    submodules = [f"{module}.{alias.name}" for alias in node.names]
    return [module, *(name for name in submodules if _is_module(root, name))]


def _command_modules(node: ast.AST, path: str, root: str) -> List[str]:
    found = []
    for module in _imported_modules(node, path, root):
        match = COMMAND_MODULE.match(module)
        if match and match.group(0) not in found:
            found.append(match.group(0))
    return found


def _token(node: ast.AST, docstrings: Set[int]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if id(node) in docstrings:
            return ""
        return node.value if node.value in COMMAND_KEYS else _feature_word(node.value)
    if isinstance(node, ast.Attribute):
        return node.attr if node.attr in KEY_CONSTANTS else _feature_word(node.attr)
    if isinstance(node, ast.Name):
        return node.id if node.id in KEY_CONSTANTS else _feature_word(node.id)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return _feature_word(node.name)
    if isinstance(node, ast.arg):
        return _feature_word(node.arg)
    if isinstance(node, ast.keyword) and node.arg:
        return _feature_word(node.arg)
    return ""


def scan(path: str, root: str = ROOT) -> List[Finding]:
    """Every command key, key constant, feature word and command import in `path`."""
    with open(os.path.join(root, path), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    docstrings = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        modules = _command_modules(node, path, root)
        found += [(path, line, module) for module in modules]
        token = _token(node, docstrings)
        if token:
            found.append((path, line, token))
    return sorted(set(found))


@functools.lru_cache(maxsize=None)
def findings() -> Tuple[Finding, ...]:
    """What the generic layers name today, file by file."""
    return tuple(finding for path in _generic_files() for finding in scan(path))


def test_every_generic_layer_exists():
    missing = [
        layer
        for layer in GENERIC_LAYERS
        if not os.path.exists(os.path.join(ROOT, layer))
    ]
    assert missing == [], "a generic layer moved: point GENERIC_LAYERS at its new path"


def test_the_generic_layers_never_name_a_command():
    unlisted = [
        f"{path}:{line} {token}"
        for path, line, token in findings()
        if (path, token) not in NAMING_EXCEPTIONS
    ]
    assert unlisted == [], "\n".join([RULE, *unlisted])


def test_every_naming_exception_still_matches():
    found = {(path, token) for path, _line, token in findings()}
    stale = sorted(
        f"{path} {token} (listed for the {pull_request})"
        for (path, token), pull_request in NAMING_EXCEPTIONS.items()
        if (path, token) not in found
    )
    assert stale == [], "\n".join(
        ["the design-pattern PR moved these: drop them from NAMING_EXCEPTIONS", *stale]
    )


def test_the_scan_reads_names_literals_constants_and_imports(tmp_path, monkeypatch):
    """Camel case, a hyphen, a key constant, a lazy import; never prose or a name."""
    monkeypatch.setitem(globals(), "KEY_CONSTANTS", frozenset({"GREET_KEY"}))
    for package in ("core", "commands/help", "forms/features"):
        (tmp_path / "app" / package).mkdir(parents=True)
    (tmp_path / "app" / "commands" / "help" / "service.py").write_text("")
    (tmp_path / "app" / "forms" / "features" / "__init__.py").write_text("")
    (tmp_path / "app" / "core" / "sample.py").write_text(
        '"""Sends the birthday."""\n'
        "from app.commands.help import service\n"
        "from app.forms.features import feature_keys\n"
        "class DefaultRolesView:\n"
        "    def pick(self):\n"
        "        # a twitch comment\n"
        "        from app.commands.setup.service import open_setup\n"
        "        key = Commands.GREET_KEY\n"
        '        return ["block-links", "translate", key, feature_keys]\n'
    )
    found = scan("app/core/sample.py", str(tmp_path))

    assert [(line, token) for _path, line, token in found] == [
        (2, "app.commands.help"),
        (4, "default_roles"),
        (7, "app.commands.setup"),
        (8, "GREET_KEY"),
        (9, "block_links"),
        (9, "translate"),
    ]

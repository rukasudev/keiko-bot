"""Generic layers never name a command (code-style rule 22).

A module of the generic layers holds no command key, no constant that holds one, no
feature word in a name or a string literal, and no import of a command's module; the
places that still do are listed in EXCEPTIONS until the design-pattern pull request
moves them, and a listed place that no longer matches fails as stale. The scan reads
the syntax tree, so comments and docstrings are prose and do not count.

A command key is every key `Commands.SETUP_FEATURES` lists plus admin, config,
log_inspection, subscriptions and translate, the commands with a module of their own
whose key is no platform word (help, setup and report also name the form's help
aside, its setup mode and a listener step, and their modules are refused as imports).
A feature word is matched inside snake case, camel case and hyphenated text alike;
`greeting_sent` is the server's own record that Keiko greeted it, not the greeting.
"""

import ast
import functools
import os
import re
from typing import Dict, Iterator, List, Set, Tuple

import pytest

from app.constants import Commands, Feature

pytestmark = pytest.mark.unit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERIC_LAYERS = (
    "app/data/guild",
    "app/data/keiko",
    "app/data/util.py",
    "app/core",
    "app/views",
    "app/components",
    "app/settings/form",
    "app/settings/discord",
    "app/settings/features/__init__.py",
    "app/settings/features/feature.py",
    "app/cogs/events.py",
    "app/webhooks/reminder.py",
    "app/webhooks/jobs.py",
    "app/migrations/runner.py",
    "app/migrations/helpers.py",
    "app/migrations/__main__.py",
)
FEATURE_KEYS = frozenset(feature["command_key"] for feature in Commands.SETUP_FEATURES)
COMMAND_KEYS = FEATURE_KEYS | {
    "admin",
    "config",
    "log_inspection",
    "subscriptions",
    "translate",
}
KEY_CONSTANTS = frozenset(
    {
        name
        for name, value in vars(Commands).items()
        if isinstance(value, str) and value in FEATURE_KEYS
    }
    | set(Feature.__members__)
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
COMMAND_MODULE = re.compile(
    r"app\.services\.\w+"
    r"|app\.cogs\.(?!events\b)\w+"
    r"|app\.settings\.features\.(?!feature\b)\w+"
    r"|app\.webhooks\.birthday_handler\b"
)
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


EXCEPTIONS: Dict[Tuple[str, str], str] = {
    **_listed(
        "app/core/cogs.py", "REMINDERS_BIRTHDAY_KEY", "app.services.reminders_birthdays"
    ),
    **_listed("app/core/utils.py", "twitch", "welcome"),
    **_listed(
        "app/views/greetings.py", "greeting", "app.services.help", "app.services.setup"
    ),
    **_listed("app/views/log_inspection.py", "inspection"),
    **_listed("app/views/user_inspection.py", "inspection"),
    **_listed(
        "app/components/buttons.py",
        "streamer",
        "app.services.notifications_twitch",
        "app.services.subscriptions",
    ),
    **_listed("app/components/embed.py", "welcome"),
    **_listed("app/settings/form/actions/configuration_card.py", "birthday"),
    **_listed(
        "app/settings/form/responses/validations.py", "streamer", "twitch", "youtube"
    ),
    **_listed(
        "app/cogs/events.py",
        "block_links",
        "default_roles",
        "greeting",
        "stream_elements",
        "welcome",
        "BLOCK_LINKS_KEY",
        "DEFAULT_ROLES_KEY",
        "WELCOME_MESSAGES_KEY",
        "app.services.block_links",
        "app.services.default_roles",
        "app.services.help",
        "app.services.stream_elements",
        "app.services.welcome_messages",
    ),
    **_listed(
        "app/webhooks/reminder.py",
        "birthday",
        "youtube",
        "app.services.notifications_youtube_video",
        "app.services.reminders_birthdays",
        "app.webhooks.birthday_handler",
    ),
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


def _is_module(root: str, dotted: str) -> bool:
    base = os.path.join(root, *dotted.split("."))
    return os.path.isfile(f"{base}.py") or os.path.isfile(
        os.path.join(base, "__init__.py")
    )


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
        if (path, token) not in EXCEPTIONS
    ]
    assert unlisted == [], "\n".join([RULE, *unlisted])


def test_every_exception_still_matches():
    found = {(path, token) for path, _line, token in findings()}
    stale = sorted(
        f"{path} {token} (listed for the {pull_request})"
        for (path, token), pull_request in EXCEPTIONS.items()
        if (path, token) not in found
    )
    assert stale == [], "\n".join(
        ["the design-pattern PR moved these: drop them from EXCEPTIONS", *stale]
    )


def test_the_scan_reads_names_literals_constants_and_imports(tmp_path):
    """Camel case, a hyphen, a key constant, a lazy import; never prose or a name."""
    for package in ("core", "services", "settings/features"):
        (tmp_path / "app" / package).mkdir(parents=True)
    (tmp_path / "app" / "services" / "help.py").write_text("")
    (tmp_path / "app" / "settings" / "features" / "__init__.py").write_text("")
    (tmp_path / "app" / "core" / "sample.py").write_text(
        '"""Sends the birthday."""\n'
        "from app.services import help\n"
        "from app.settings.features import feature_keys\n"
        "class DefaultRolesView:\n"
        "    def pick(self):\n"
        "        # a twitch comment\n"
        "        from app.services.setup import open_setup\n"
        "        key = Commands.REMINDERS_BIRTHDAY_KEY\n"
        '        return ["block-links", "translate", key, feature_keys]\n'
    )
    found = scan("app/core/sample.py", str(tmp_path))

    assert [(line, token) for _path, line, token in found] == [
        (2, "app.services.help"),
        (4, "default_roles"),
        (7, "app.services.setup"),
        (8, "REMINDERS_BIRTHDAY_KEY"),
        (9, "block_links"),
        (9, "translate"),
    ]

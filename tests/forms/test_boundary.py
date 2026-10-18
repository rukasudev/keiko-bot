"""The lines the form platform never crosses.

`app/settings/` imports `discord` only under `discord/`, the form engine
imports nothing else from `app` except `app.settings` and `app.constants`, no
module names a command key, no module blocks the event loop, and no comment
tells a story.
"""

import ast
import os
import re

import pytest

pytestmark = pytest.mark.unit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS = os.path.join(ROOT, "app", "settings")
ADAPTER = os.path.join("app", "settings", "discord")
PLATFORM = (os.path.join("app", "settings", "form"),)

IMPORTS_DISCORD = re.compile(r"^\s*(?:from|import)\s+discord\b", re.MULTILINE)
IMPORTS_APP = re.compile(r"^\s*(?:from|import)\s+(app(?:\.[\w.]+)?)\b", re.MULTILINE)
BLOCKING = re.compile(
    r"^\s*(?:from|import)\s+(?:requests|pymongo)\b|\btime\.sleep\(", re.MULTILINE
)
COMMAND_KEY = re.compile(r"\bcommand_key\b")
REFLECTION = re.compile(r"\b(?:hasattr|getattr)\(")
COMMENT_BLOCK = re.compile(r"^[ \t]*#(?!!).*\n(?:[ \t]*#.*\n)+", re.MULTILINE)
DEFINITION = re.compile(r"^(?:def|class) ([A-Za-z_][\w]*)", re.MULTILINE)
SECTION_MARKER = re.compile(r"^[ \t]*#\s*(?:-{3,}|={3,})", re.MULTILINE)
MANAGER = "app.settings.form.manager"
FORM_YAML = os.path.join(SETTINGS, "form", "form_yaml.py")
WORDS = {"id"}


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
        path for path in python_files(SETTINGS) if _relative(path).startswith(PLATFORM)
    ]


def _outside_adapter():
    return [path for path in python_files(SETTINGS) if ADAPTER not in _relative(path)]


def test_only_the_discord_adapter_imports_discord():
    assert _offenders(IMPORTS_DISCORD, _outside_adapter()) == []


def test_the_platform_imports_nothing_internal_but_itself_and_constants():
    offenders = []
    for path in _platform_files():
        for module in IMPORTS_APP.findall(_source(path)):
            if not module.startswith(("app.settings", "app.constants")):
                offenders.append((_relative(path), module))
    assert offenders == []


def test_the_platform_never_names_a_command_key():
    assert _offenders(COMMAND_KEY, _platform_files()) == []


def test_nothing_in_the_platform_blocks_the_event_loop():
    assert _offenders(BLOCKING, list(python_files(SETTINGS))) == []


def test_no_reflection_outside_the_adapter():
    assert _offenders(REFLECTION, _outside_adapter()) == []


def test_no_comment_block_longer_than_one_line():
    assert _offenders(COMMENT_BLOCK, list(python_files(SETTINGS))) == []


def _short_names(path):
    with open(path) as handle:
        tree = ast.parse(handle.read())

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            name = node.id
        elif isinstance(node, ast.arg):
            name = node.arg
        else:
            continue
        if len(name) <= 2 and name not in WORDS and not name.startswith("_"):
            found.add(name)
    return sorted(found)


def test_a_name_says_what_it_holds():
    offenders = {
        _relative(path): _short_names(path)
        for path in python_files(SETTINGS)
        if _short_names(path)
    }
    assert offenders == {}


def test_no_section_markers():
    assert _offenders(SECTION_MARKER, list(python_files(SETTINGS))) == []


def _module_of(path):
    return ".".join(os.path.splitext(_relative(path))[0].split(os.sep))


def _imported_modules(path):
    """Every module a file imports, `from package import name` as `package.name`."""
    package = _module_of(path).rsplit(".", 1)[0].split(".")
    found = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, node.module] if node.module else base)
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def test_the_form_never_imports_the_manager():
    """The manager decides on its own and opens the form as a child session;
    the form, its actions and its responses know nothing about it."""
    form_files = [
        path
        for path in python_files(os.path.join(SETTINGS, "form"))
        if _module_of(path) != MANAGER
    ]
    offenders = [
        _relative(path)
        for path in form_files
        if any(
            module == MANAGER or module.startswith(f"{MANAGER}.")
            for module in _imported_modules(path)
        )
    ]
    assert offenders == []


def _section_classes():
    """Every card section model of the schema, the union and its base included."""
    tree = ast.parse(_source(FORM_YAML))
    found = {"Section", "SectionBase"}
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    while True:
        more = {
            node.name
            for node in classes
            if any(
                isinstance(base, ast.Name) and base.id in found for base in node.bases
            )
        }
        if more <= found:
            return found
        found |= more


def _names(node):
    """The class names an expression mentions: a name, a tuple, a sum of them."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set().union(*(_names(element) for element in node.elts))
    if isinstance(node, ast.BinOp):
        return _names(node.left) | _names(node.right)
    return set()


def _is_call_of(node, name):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    )


def _section_dispatches(path, sections):
    """The lines that pick behaviour by a section's class: isinstance, type(), match."""
    tree = ast.parse(_source(path))
    named = set(sections)
    for node in tree.body:
        if isinstance(node, ast.Assign) and _names(node.value) & sections:
            named |= {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }

    found = []
    for node in ast.walk(tree):
        if _is_call_of(node, "isinstance") and len(node.args) == 2:
            if _names(node.args[1]) & named:
                found.append(node.lineno)
        elif isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            typed = any(_is_call_of(side, "type") for side in sides)
            if typed and set().union(*(_names(side) for side in sides)) & named:
                found.append(node.lineno)
        elif isinstance(node, ast.MatchClass) and _names(node.cls) & named:
            found.append(node.lineno)
    return found


def test_no_card_section_is_dispatched_by_its_class():
    """A section type contributes through its `SectionKind` and the accessors
    of its model; 45 `isinstance` checks on section classes used to pick it."""
    sections = _section_classes()
    offenders = {
        _relative(path): _section_dispatches(path, sections)
        for path in python_files(SETTINGS)
        if _section_dispatches(path, sections)
    }
    assert offenders == {}


def test_the_checks_see_what_a_regular_expression_would_miss(tmp_path):
    """Parenthesized imports, a manager among other names, `type(x) is`, aliases."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from app.settings.form import (\n    conditions,\n    manager,\n)\n"
        "WITH_MODE = (TitleContentSection, FileUploadSection)\n"
        "def pick(section):\n"
        "    if type(section) is ValueSelectSection:\n"
        "        return 1\n"
        "    return isinstance(section, WITH_MODE + (ModalInputSection,))\n"
    )
    assert MANAGER in _imported_modules(str(sample))
    assert _section_dispatches(str(sample), _section_classes()) == [7, 9]


def _response_helper_files():
    return python_files(os.path.join(ROOT, "app", "settings", "form", "responses"))


def _service_files():
    paths = []
    for part in ("services", "views", "components", "cogs", "webhooks", "api"):
        paths += [
            path
            for path in python_files(os.path.join(ROOT, "app", part))
            if not path.endswith("_test.py")
        ]
    return paths


def _public_names(paths):
    found = set()
    for path in paths:
        tree = ast.parse(_source(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.add(node.name)
            elif isinstance(node, ast.Assign):
                found.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
    return {name for name in found if not name.startswith("_")}


def test_the_services_never_redefine_a_name_the_responses_package_owns():
    """Broke as: the services layer kept its own parse_link and its own
    calendar beside the platform's, and the two had already drifted while both
    were live.

    This compares names, not bodies: a copy under a different name still
    passes. It is the cheap half of "one owner per helper"; the expensive
    half is reading the diff.
    """
    duplicated = _public_names(_response_helper_files()) & _public_names(
        _service_files()
    )
    assert sorted(duplicated) == []

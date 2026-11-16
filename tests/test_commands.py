"""Every real command, as the registry lists it, is whole.

Each folder of `app/commands/` exposes its `COMMAND` and the list holds each one once,
under a key no other command uses. A command's copy and form are the `copy.yml` and
the `form.yml` of its folder, the only YAML files there, and the form compiles. Every `ml("...")` literal in a command's folder
resolves in en-us and in pt-br, every `analytics.record("...")` literal is an event of
the catalog, every reminder title and webhook event has one owner, and every key
looked up by its literal in `app/` is declared. A webhook event
is a key of `Command.webhooks` when the registry declares that field, or else a field
of `Hooks` that is not a Discord listener. The checks start once the list holds
`Command` objects.
"""

import ast
import dataclasses
import importlib
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
import yaml

from tests.helpers.commands import form_compiler

pytestmark = pytest.mark.unit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS = os.path.join(ROOT, "app", "commands")
REGISTRY = ("app/core/commands.py", "app/commands/__init__.py")
CATALOG = os.path.join(ROOT, "app", "analytics", "catalog.yml")
LOCALES = ("en-us", "pt-br")
LISTENER_HOOKS = frozenset(
    {"member_joined", "message_sent", "prefixed_message", "message_edited"}
)
NO_REGISTRY = (
    "app.commands.COMMANDS does not list Command objects yet (app/core/commands.py, "
    "app/commands/__init__.py): the command checks start with them"
)


def _registered() -> bool:
    if not all(os.path.isfile(os.path.join(ROOT, path)) for path in REGISTRY):
        return False
    listed = importlib.import_module("app.commands").COMMANDS
    command = importlib.import_module("app.core.commands").Command
    return bool(listed) and all(isinstance(item, command) for item in listed)


if not _registered():
    pytest.skip(NO_REGISTRY, allow_module_level=True)


@pytest.fixture(scope="module")
def registry() -> Tuple[List[Any], Any, Any]:
    listed = importlib.import_module("app.commands").COMMANDS
    core = importlib.import_module("app.core.commands")
    return list(listed), core.Command, core.Hooks


def _folders() -> List[str]:
    return sorted(
        name
        for name in os.listdir(COMMANDS)
        if os.path.isdir(os.path.join(COMMANDS, name))
        and not name.startswith(("_", "."))
        and set(os.listdir(os.path.join(COMMANDS, name))) - {"__pycache__"}
    )


def _declared(folder: str) -> Any:
    return getattr(importlib.import_module(f"app.commands.{folder}"), "COMMAND", None)


def _python_files(folder: str) -> Iterator[Path]:
    for path in sorted(Path(COMMANDS, folder).rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def _literal_calls(folder: str, matches, keyword: str) -> Iterator[Tuple[str, str]]:
    for path in _python_files(folder):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        where = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not matches(node.func):
                continue
            arguments = [*node.args[:1]]
            arguments += [item.value for item in node.keywords if item.arg == keyword]
            for argument in arguments:
                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    yield f"{where}:{node.lineno}", argument.value


def _is_ml(func: ast.AST) -> bool:
    name = func.attr if isinstance(func, ast.Attribute) else ""
    return name == "ml" or (isinstance(func, ast.Name) and func.id == "ml")


def _is_record(func: ast.AST) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "record"
        and isinstance(func.value, ast.Name)
        and func.value.id == "analytics"
    )


def _owners(pairs: Iterator[Tuple[str, str]]) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = defaultdict(list)
    for name, key in pairs:
        found[name].append(key)
    return {name: keys for name, keys in found.items() if len(keys) > 1}


def test_every_folder_declares_its_command(registry):
    _listed, command_type, _hooks = registry
    missing = [
        folder
        for folder in _folders()
        if not os.path.isfile(os.path.join(COMMANDS, folder, "__init__.py"))
        or not isinstance(_declared(folder), command_type)
    ]
    assert missing == [], (
        f"an __init__.py with COMMAND = Command(...) is missing: {missing}"
    )


def test_the_list_holds_every_folder_once(registry):
    listed, _command, _hooks = registry
    declared = {folder: _declared(folder) for folder in _folders()}
    ids = [id(command) for command in listed]
    unlisted = [
        folder for folder, command in declared.items() if id(command) not in ids
    ]
    folders = {id(command) for command in declared.values()}
    strays = [command.key for command in listed if id(command) not in folders]
    twice = sorted({command.key for command in listed if ids.count(id(command)) > 1})

    problems = [
        *(
            f"add {folder} to COMMANDS in app/commands/__init__.py"
            for folder in unlisted
        ),
        *(f"COMMANDS lists {key}, which has no folder" for key in strays),
        *(f"COMMANDS lists {key} twice" for key in twice),
    ]
    assert problems == []


def test_every_key_is_unique(registry):
    listed, _command, _hooks = registry
    keys = [command.key for command in listed]
    shared = sorted({key for key in keys if keys.count(key) > 1})
    assert shared == [], "a key is the _id in guild.cogs: two commands never share one"


def test_a_command_writes_its_copy_and_its_form_in_its_folder_copy_and_form_yml():
    from app.core.commands import COPY_FILE, FORM_FILE, copy_files, form_files

    compile_form = form_compiler()
    read = {COPY_FILE: copy_files(), FORM_FILE: form_files()}
    problems = []

    for folder in _folders():
        command = _declared(folder)
        strays = sorted(
            name
            for name in os.listdir(Path(COMMANDS, folder))
            if name.endswith((".yml", ".yaml")) and name not in read
        )
        if strays:
            problems.append(
                f"{folder}: {strays} is never read, write it in {' or '.join(read)}"
            )
        for name, found in read.items():
            on_disk = Path(COMMANDS, folder, name)
            if not on_disk.exists():
                if command.key in found:
                    problems.append(f"{folder}: {command.key} reads {found[command.key]}")
            elif found.get(command.key) != on_disk:
                problems.append(f"{folder}: {command.key} does not read {folder}/{name}")
        form = Path(COMMANDS, folder, FORM_FILE)
        if not form.exists():
            continue
        try:
            compile_form(command.key, yaml.safe_load(form.read_text("utf-8")))
        except Exception as error:
            problems.append(f"{folder}: {type(error).__name__}: {error}")

    assert problems == []


def test_every_copy_key_a_command_uses_exists_in_both_locales():
    import i18n

    missing = [
        f"{where} {key} ({locale})"
        for folder in _folders()
        for where, key in _literal_calls(folder, _is_ml, "key")
        for locale in LOCALES
        if i18n.t(key, locale=locale) == key
    ]
    assert missing == [], "\n".join(
        ["write the key in both locales of the command's copy.yml", *missing]
    )


def _sections(steps: Any) -> Iterator[Any]:
    for step in steps:
        yield from getattr(step, "sections", ())
        yield from _sections(getattr(step, "steps", ()))


def test_every_default_a_form_names_is_a_copy_block_in_both_locales():
    """A title-content section reads the title and the description it shows until
    the admin customizes them from the copy block its `default` names."""
    import i18n

    from app.forms.form.form_yaml import DiskSource, TitleContentSection, registry

    named = [
        (form, section)
        for form in DiskSource().list()
        for section in _sections(registry.get(form).steps)
        if isinstance(section, TitleContentSection)
    ]
    missing = [
        f"{form}: {section.key} names {section.default}.{part} ({locale})"
        for form, section in named
        for part in ("title", "description")
        for locale in LOCALES
        if i18n.t(f"{section.default}.{part}", locale=locale)
        == f"{section.default}.{part}"
    ]
    assert named, "no form writes a title-content section: drop this check"
    assert missing == [], "\n".join(
        ["write the default in both locales of the command's copy.yml", *missing]
    )


def test_every_event_a_command_records_is_in_the_catalog():
    with open(CATALOG, encoding="utf-8") as handle:
        events = set(yaml.safe_load(handle)["events"])

    unknown = [
        f"{where} {event}"
        for folder in _folders()
        for where, event in _literal_calls(folder, _is_record, "type")
        if event not in events
    ]
    assert unknown == [], "\n".join(
        ["declare the event in app/analytics/catalog.yml (docs/analytics.md)", *unknown]
    )


def test_every_reminder_title_has_one_owner(registry):
    listed, command_type, _hooks = registry
    fields = {field.name for field in dataclasses.fields(command_type)}
    assert "reminders" in fields, "the contract declares Command.reminders"

    shared = _owners(
        (title, command.key) for command in listed for title in command.reminders or {}
    )
    assert shared == {}, "a reminder title is dispatched to exactly one command"


def test_every_webhook_event_has_one_owner(registry):
    listed, command_type, hooks_type = registry
    fields = {field.name for field in dataclasses.fields(command_type)}

    if "webhooks" in fields:
        pairs = (
            (event, command.key)
            for command in listed
            for event in command.webhooks or {}
        )
    else:
        events = [
            field.name
            for field in dataclasses.fields(hooks_type)
            if field.name not in LISTENER_HOOKS
        ]
        pairs = (
            (event, command.key)
            for command in listed
            for event in events
            if command.hooks is not None and getattr(command.hooks, event) is not None
        )
    assert _owners(pairs) == {}, "a webhook event is handed to exactly one command"


def _registry_lookups(name: str) -> Iterator[Tuple[str, str]]:
    for path in sorted(Path(ROOT, "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        where = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else ""
            called = func.id if isinstance(func, ast.Name) else called
            argument = node.args[0]
            if called == name and isinstance(argument, ast.Constant):
                yield f"{where}:{node.lineno}", str(argument.value)


def test_every_key_a_module_looks_up_by_its_literal_is_declared(registry):
    listed, _command, _hooks = registry
    keys = {command.key for command in listed}
    opens = {command.key for command in listed if command.opens is not None}
    offered = {command.key for command in listed if command.on_setup}

    unknown = [
        f"{where} {name}({key!r})"
        for name, known in (
            ("command_for", keys),
            ("opener", opens),
            ("setup_feature", offered),
        )
        for where, key in _registry_lookups(name)
        if key not in known
    ]
    assert unknown == [], (
        "each literal names a declared command (`opener` one with `opens`, "
        f"`setup_feature` one on /setup): {unknown}"
    )


def test_every_module_a_command_loads_as_an_extension_has_its_setup(registry):
    listed, _command, _hooks = registry
    from app.core.utils import command_extensions

    missing = [
        module
        for command in listed
        for module in command_extensions(command)
        if not callable(getattr(importlib.import_module(module), "setup", None))
    ]
    assert missing == [], (
        f"add `async def setup(bot)` that adds the cog to {missing}: load_extension "
        "raises NoEntryPointError without it"
    )


async def test_every_cog_a_command_declares_is_added_by_its_extension(registry):
    listed, _command, _hooks = registry
    from unittest.mock import AsyncMock, MagicMock

    from discord import app_commands

    from app.core.utils import command_extensions

    left_out = []
    for command in listed:
        bot = MagicMock()
        bot.add_cog = AsyncMock()
        for module in command_extensions(command):
            await importlib.import_module(module).setup(bot)
        added = {type(call.args[0]) for call in bot.add_cog.await_args_list}
        left_out += [
            f"{command.key}: {cog.__name__}"
            for cog in command.cogs
            if not issubclass(cog, app_commands.Group) and cog not in added
        ]
    assert left_out == [], (
        f"the setup(bot) of each cog's module adds it; these are declared and never "
        f"added: {left_out}"
    )


def test_a_lazy_command_loads_once_keiko_is_ready_and_the_rest_at_startup(registry):
    listed, _command, _hooks = registry
    from app.core.utils import (
        command_extensions,
        extension_paths,
        lazy_commands,
        startup_extensions,
    )

    startup = startup_extensions()
    lazy = [module for key in lazy_commands() for module in extension_paths(key)]
    every = {module for command in listed for module in command_extensions(command)}

    assert set(lazy).isdisjoint(startup)
    assert every <= set(startup) | set(lazy)
    assert len(startup) == len(set(startup))
    assert lazy_commands() == ["config", "admin"]
    assert extension_paths("admin") == [
        "app.commands.admin.cog",
        "app.commands.admin.log",
        "app.commands.admin.digest",
    ]
    assert extension_paths("events") == ["app.core.events"]

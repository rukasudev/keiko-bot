"""A cog starts its background loops when the bot loads it, never when it is built.

Broke as: the /birthday cog (`app/commands/birthdays/cog.py`) started
`reconcile_reminders` in its `__init__`, so a test that only built it (the slash
command contract builds every cog on a bot that never logs in) left the loop running;
its `before_loop` failed with "Client has not been properly initialised", and the
never-retrieved failure was logged whenever the garbage collector ran, inside whatever
test was running then: the Twitch fan-out tests, which assert no ERROR in their caplog,
failed now and then. The analytics jobs (`app/core/jobs.py`) and the weekly digest
(`app/commands/admin/digest.py`) still started theirs in `__init__`, the debug log
writer (`app/core/debug_logs.py`) created its task in its constructor, and reloading
`app.core.monitoring` failed: its unload leaves the library's `PrometheusCog` on the
bot, so its setup added a second one and raised "already loaded".

Shared behaviour: the loop lifecycle discord.py gives a cog, `cog_load` when the bot
adds it and `cog_unload` when it removes it, the way `Events` and `Heartbeat` start
theirs. Exposed by: the flaky Twitch tests of `tests/test_notifications_twitch.py`.

Guaranteed, for every cog Keiko defines with a `tasks.loop`, found through every
extension the bot can load (and every class under `app/` that declares one is such a
cog): building the cog starts nothing; loading its extension,
the production order (`setup_hook` -> `load_extension` -> `setup` -> `add_cog` ->
`cog_load`), starts each of its loops once, a reload leaves exactly one of each
running, and unloading stops them all. No constructor under `app/` starts a loop or
creates a task.
"""

import ast
import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import discord
import pytest
from discord.ext import commands, tasks

from app.constants import Dependencies
from app.core.utils import extension_names, extension_paths

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

APP = Path(__file__).resolve().parents[3] / "app"
STARTERS = {"start", "create_task", "ensure_future"}


def _extensions():
    return list(
        dict.fromkeys(
            module for name in extension_names() for module in extension_paths(name)
        )
    )


def _loops(owner):
    return sorted(
        {
            name
            for klass in owner.__mro__
            for name, value in vars(klass).items()
            if isinstance(value, tasks.Loop)
        }
    )


def _owners():
    found = []
    for extension in _extensions():
        module = importlib.import_module(extension)
        for owner in vars(module).values():
            if (
                isinstance(owner, type)
                and issubclass(owner, commands.Cog)
                and owner.__module__ == extension
                and _loops(owner)
            ):
                found.append(pytest.param(extension, owner, id=owner.__qualname__))
    return found


OWNERS = _owners()


def _bot():
    bot = commands.Bot(
        command_prefix="!", intents=discord.Intents.none(), help_command=None
    )
    for name in ("config", "twitch", "youtube", "reminder", "notion"):
        setattr(bot, name, MagicMock())
    bot.all_cogs = {}
    bot.app_commands = []
    return bot


def _task_names(owner):
    return {
        f"discord-ext-tasks: {getattr(owner, name).coro.__qualname__}"
        for name in _loops(owner)
    }


def _running(owner):
    names = _task_names(owner)
    return sorted(
        (
            task
            for task in asyncio.all_tasks()
            if task.get_name() in names and not task.done()
        ),
        key=asyncio.Task.get_name,
    )


async def _settled():
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _stops_without_waiting(monkeypatch):
    monkeypatch.setattr(Dependencies, "BLOCKING_IO_STOP_WAIT_SECONDS", 0.01)


def _sources():
    for path in sorted(APP.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _declares_a_loop(node):
    return any(
        ast.unparse(getattr(decorator, "func", decorator)) in {"tasks.loop", "loop"}
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in item.decorator_list
    )


def test_every_class_that_declares_a_loop_is_a_cog_an_extension_adds():
    declared = {
        node.name
        for _path, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _declares_a_loop(node)
    }

    assert declared and declared == {param.values[1].__name__ for param in OWNERS}


@pytest.mark.parametrize(("extension", "owner"), OWNERS)
async def test_building_a_cog_starts_none_of_its_loops(extension, owner):
    async with _bot() as bot:
        cog = owner(bot)
        await _settled()
        started = [name for name in _loops(owner) if getattr(cog, name).is_running()]
        running = _running(owner)
        for task in running:
            task.cancel()

        assert started == []
        assert running == []


@pytest.mark.parametrize(("extension", "owner"), OWNERS)
async def test_the_bot_starts_each_loop_once_it_loads_the_cog_and_stops_it_on_unload(
    extension, owner, monkeypatch
):
    monkeypatch.setitem(sys.modules, extension, sys.modules[extension])
    expected = len(_loops(owner))
    async with _bot() as bot:
        await bot.load_extension(extension)
        await _settled()
        loaded = _running(owner)

        await bot.reload_extension(extension)
        await _settled()
        reloaded = _running(owner)

        await bot.unload_extension(extension)
        await _settled()

        assert len(loaded) == expected
        assert len(reloaded) == expected and set(reloaded).isdisjoint(loaded)
        assert all(task.cancelled() for task in loaded)
        assert _running(owner) == []


def _starts_in_constructors():
    found = []
    for path, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "__init__":
                continue
            found += [
                f"{path.relative_to(APP.parent)}:{call.lineno} {ast.unparse(call)}"
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in STARTERS
            ]
    return found


def test_no_constructor_starts_a_loop_or_creates_a_task():
    assert _starts_in_constructors() == [], (
        "start loops and tasks in `cog_load` (or the owner's own start), never in "
        "`__init__`: an object built and never loaded leaves them running"
    )

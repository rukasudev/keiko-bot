"""A cog starts its background loop when the bot loads it, never when it is built.

Broke as: the /birthday cog (`app/commands/birthdays/cog.py`) started
`reconcile_reminders` in its `__init__`, so a test that only built it (the slash
command contract builds every cog on a bot that never logs in) left the loop running;
its `before_loop` failed with "Client has not been properly initialised", and the
never-retrieved failure was logged whenever the garbage collector ran, inside whatever
test was running then: the Twitch fan-out tests, which assert no ERROR in their caplog,
failed now and then.

Shared behaviour: the loop lifecycle discord.py gives a cog, `cog_load` when the bot
adds it and `cog_unload` when it removes it, the way `Events` and `Heartbeat` start
theirs. Exposed by: the flaky Twitch tests of `tests/test_notifications_twitch.py`.

Guaranteed: building the cog starts nothing; loading its extension, the production
order (`setup_hook` -> `load_extension` -> `setup` -> `add_cog` -> `cog_load`), starts
the loop once, a reload leaves exactly one running, and unloading stops it.
"""

import asyncio
import sys

import discord
import pytest
from discord.ext import commands

from app.commands.birthdays.cog import Birthday

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

EXTENSION = "app.commands.birthdays.cog"
TASK = "discord-ext-tasks: Birthday.reconcile_reminders"


def _bot():
    return commands.Bot(
        command_prefix="!", intents=discord.Intents.none(), help_command=None
    )


def _running():
    return [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == TASK and not task.done()
    ]


async def _settled():
    for _ in range(3):
        await asyncio.sleep(0)


async def test_building_the_birthday_cog_starts_no_loop():
    async with _bot() as bot:
        cog = Birthday(bot)
        await _settled()

        assert not cog.reconcile_reminders.is_running()
        assert _running() == []


async def test_the_bot_starts_the_loop_once_it_loads_the_cog_and_stops_it_on_unload(
    monkeypatch,
):
    monkeypatch.setitem(sys.modules, EXTENSION, sys.modules[EXTENSION])
    async with _bot() as bot:
        await bot.load_extension(EXTENSION)
        await _settled()
        loaded = _running()

        await bot.reload_extension(EXTENSION)
        await _settled()
        reloaded = _running()

        await bot.unload_extension(EXTENSION)
        await _settled()

        assert len(loaded) == 1
        assert len(reloaded) == 1 and reloaded != loaded
        assert loaded[0].cancelled()
        assert _running() == []

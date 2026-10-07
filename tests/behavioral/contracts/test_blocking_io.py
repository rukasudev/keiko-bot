"""Blocking calls run on Keiko's own pool of threads, and a stop never waits for one.

`asyncio.to_thread` hands its call to the loop's default executor. asyncio creates that
pool lazily with `min(32, cpus + 4)` threads, five on the 1 vCPU the bot runs on, and the
Twitch waits sleep in them for up to 30 seconds: two lives at once left three threads for
every Mongo read, Redis call and DNS lookup of the whole bot. And when the loop closes,
`asyncio.run` joins that pool for up to five minutes, so one call stuck in a thread held a
SIGTERM past Docker's 30 second grace, and the queues `lifecycle.run` writes after the
loop never reached Mongo.

Guaranteed: from the moment the bot sets itself up, before any cog loads, every blocking
call runs on a pool of `Dependencies.BLOCKING_IO_THREADS` threads named `keiko-io`; and
closing the loop drops the calls still queued and waits for none under way. Every test
runs its own loop and closes it the way `asyncio.run` does inside discord.py's `Bot.run`.
"""
import asyncio
import importlib
import threading
import time
from types import SimpleNamespace

import discord
import pytest

from app import lifecycle
from app.bot import DiscordBot
from app.constants import Dependencies

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("lifecycle")]


@pytest.fixture(autouse=True)
def forget_the_pool(monkeypatch):
    """Each test installs its own pool; none is left for the stop of a later test."""
    monkeypatch.setattr(lifecycle, "_POOL", None)


def thread_name():
    return threading.current_thread().name


def run(coroutine):
    """`asyncio.run`, on a loop of its own that leaves the suite's loop alone."""
    with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
        return runner.run(coroutine)


def test_a_blocking_call_runs_on_keikos_own_pool():
    async def main():
        lifecycle.install_blocking_io()
        return await asyncio.to_thread(thread_name)

    assert run(main()).startswith("keiko-io")


def test_the_pool_runs_as_many_calls_at_once_as_it_has_threads():
    started = []
    release = threading.Event()

    def call():
        started.append(thread_name())
        release.wait(5)

    async def main():
        lifecycle.install_blocking_io()
        loop = asyncio.get_running_loop()
        calls = [
            loop.run_in_executor(None, call)
            for _ in range(Dependencies.BLOCKING_IO_THREADS + 3)
        ]
        await asyncio.sleep(0.3)
        running = len(started)
        release.set()
        await asyncio.gather(*calls)
        return running

    assert run(main()) == Dependencies.BLOCKING_IO_THREADS


def test_closing_the_loop_never_waits_for_a_call_under_way():
    released = threading.Event()

    async def main():
        lifecycle.install_blocking_io()
        asyncio.get_running_loop().run_in_executor(None, released.wait, 30)
        await asyncio.sleep(0.05)

    started = time.monotonic()
    try:
        run(main())
        elapsed = time.monotonic() - started
    finally:
        released.set()

    assert elapsed < 2, f"closing the loop waited {elapsed:.1f}s for a stuck call"


def test_closing_the_loop_drops_the_calls_still_queued():
    released = threading.Event()
    ran = []

    async def main():
        lifecycle.install_blocking_io()
        loop = asyncio.get_running_loop()
        for _ in range(Dependencies.BLOCKING_IO_THREADS):
            loop.run_in_executor(None, released.wait, 30)
        loop.run_in_executor(None, ran.append, "queued")
        await asyncio.sleep(0.05)

    try:
        run(main())
    finally:
        released.set()
    time.sleep(0.2)

    assert ran == [], "a call that never started is not started after the stop"


def test_the_bot_installs_the_pool_before_any_cog_loads(monkeypatch):
    seen = []

    async def load(bot, mode, cogs, sync=False):
        seen.append(await asyncio.to_thread(thread_name))

    async def no_translator(translator):
        return None

    monkeypatch.setattr(importlib.import_module("app.bot"), "cogs_manager", load)
    bot = DiscordBot(SimpleNamespace(
        PREFIX="ke!",
        APPLICATION_ID=1,
        OWNER_ID=2,
        STATUS=discord.Status.online,
        ACTIVITY=discord.ActivityType.playing,
        DESCRIPTION="Keiko",
        WEBHOOK_URL="https://keiko.test/v1/webhooks",
        NOTION_TOKEN="token",
        NOTION_DATABASE_ID="database",
        NOTION_ENABLED=False,
        YOUTUBE_API_KEY="key",
        REMINDER_APPLICATION_ID="1",
        REMINDER_API_KEY="api-key",
    ))
    monkeypatch.setattr(bot.tree, "set_translator", no_translator)

    run(bot.setup_hook())

    assert len(seen) == 1 and seen[0].startswith("keiko-io")

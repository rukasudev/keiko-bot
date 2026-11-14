"""A thread job that reads Mongo while every pool thread does the same still gets its
answer, and a read that can never be answered gives its thread back.

Broke as: the bot's pool of `Dependencies.BLOCKING_IO_THREADS` threads was the loop's
default executor, which also runs `loop.getaddrinfo` and the TLS handshake of every
new Mongo connection. A thread job reading Mongo blocks its thread in `app.data.wait`
until the loop answers; with every pool thread waiting there on reads that needed a new
connection, the host name lookup those reads waited for queued behind them for good,
and every message check, settings read and notifier of the bot hung with them.

Shared behaviour: the thread seam (`app.core.threads.to_thread`) and `app.data.wait`.
Consumer that exposed it: block links reads its settings on a thread for every message.
Guaranteed: blocked links are still deleted when a burst of messages fills the pool
while Mongo opens connections, and `wait` raises within `DBConfigs.MONGO_THREAD_WAIT_SECONDS`
instead of holding its thread forever.
"""

import asyncio
import os
import threading
import time

import pytest

import app.data as data_layer
from app.constants import DBConfigs, Dependencies
from app.core import threads
from app.data import wait
from app.services.block_links import check_message
from tests.generators import moderations
from tests.mocks import create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def run(coroutine):
    """`asyncio.run`, on a loop of its own that leaves the suite's loop alone."""
    with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
        return runner.run(coroutine)


@pytest.fixture
def forget_the_pool(monkeypatch):
    """Each test installs its own pool; none is left for a later test."""
    monkeypatch.setattr(threads, "_POOL", None)


def opening_a_connection_first(monkeypatch, deps):
    """Every settings read resolves the server's host name first, as pymongo does for
    each new connection."""
    find = deps.data.guild_cogs.find_cog_by_guild_id

    async def find_cog_by_guild_id(guild_id, key):
        await asyncio.get_running_loop().getaddrinfo("localhost", 27017)
        return await find(guild_id, key)

    monkeypatch.setattr(deps.data.guild_cogs, "find_cog_by_guild_id", find_cog_by_guild_id)


@pytest.mark.usefixtures("forget_the_pool")
def test_a_burst_of_messages_that_fills_the_pool_still_deletes_every_blocked_link(
    deps, mongodb, channel, member, monkeypatch
):
    guild_ids = [str(9000 + index) for index in range(Dependencies.BLOCKING_IO_THREADS)]
    for guild_id in guild_ids:
        moderations.seed_cog(mongodb, "block_links", {
            "guild_id": guild_id,
            "enabled": True,
            "mode": "block_all",
            "allowed_roles": {"style": "role", "values": []},
            "allowed_chats": {"style": "channel", "values": []},
            "allowed_links": {"style": "bullet", "values": []},
            "custom_links": {"style": "composition", "values": []},
            "answer": "",
        })
    opening_a_connection_first(monkeypatch, deps)
    messages = [
        create_message(channel, member, "look https://spam-site.com") for _ in guild_ids
    ]

    async def main():
        threads.install()
        data_layer.bind_loop(asyncio.get_running_loop())
        checks = [
            check_message(guild_id, message)
            for guild_id, message in zip(guild_ids, messages)
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*checks), timeout=5)
        except TimeoutError:
            return False
        return True

    finished = run(main())

    assert finished, "the message checks never finished: the pool deadlocked"
    assert [message._delete.await_count for message in messages] == [1] * len(messages)


def test_a_read_the_loop_never_answers_gives_its_thread_back(monkeypatch):
    monkeypatch.setattr(DBConfigs, "MONGO_THREAD_WAIT_SECONDS", 0.2)
    loop = asyncio.new_event_loop()
    serving = threading.Thread(target=loop.run_forever, daemon=True)
    serving.start()
    data_layer.bind_loop(loop)
    never = asyncio.Event()

    async def unanswered():
        await never.wait()

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="Mongo"):
            wait(unanswered())
        elapsed = time.monotonic() - started
        cancelled = asyncio.run_coroutine_threadsafe(_pending(), loop).result(2)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        serving.join(2)
        loop.close()

    assert elapsed < 2
    assert cancelled == 0, "the read the thread gave up on still runs on the loop"


async def _pending():
    await asyncio.sleep(0.05)
    current = asyncio.current_task()
    return len([task for task in asyncio.all_tasks() if task is not current])


def test_no_module_of_the_app_hands_a_call_to_the_loops_own_threads():
    """`asyncio.to_thread` and `run_in_executor(None, ...)` run on the loop's own small
    pool, where a call that waits on the loop starves the lookups it waits for."""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "app")
    found = []
    for folder, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".py") or name == "threads.py":
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if "asyncio.to_thread" in source or "run_in_executor(" in source:
                found.append(os.path.relpath(path, root))

    assert found == [], f"hand the call to app.core.threads.to_thread instead: {found}"

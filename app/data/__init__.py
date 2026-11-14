"""The data layer: one repository per collection, built over one client at startup and
reached through `data()`."""

import asyncio
import concurrent.futures
import threading
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Coroutine, Iterator, List, Optional, TypeVar

from app.constants import DBConfigs
from app.data.analytics.counts import Counts
from app.data.analytics.keiko import Keiko
from app.data.analytics.lifetime import Lifetime
from app.data.collection import Client
from app.data.events.activity import Activity
from app.data.events.changes import Changes
from app.data.events.interactions import Interactions
from app.data.guild.analytics_archives import Archives
from app.data.guild.cogs import Cogs
from app.data.guild.members import Members
from app.data.guild.messages import Messages
from app.data.guild.settings import Settings as GuildSettings
from app.data.keiko.integrations import Integrations
from app.data.keiko.schedules import Schedules
from app.data.keiko.settings import Settings as KeikoSettings
from app.data.keiko.subscriptions import Subscriptions
from app.data.logs import Logs, LogsFiles
from app.data.migrations.changelog import Changelog
from app.data.traces.runs import Runs
from app.data.util import Databases

Answer = TypeVar("Answer")


@dataclass(frozen=True)
class Data:
    """Every repository, one per collection, over the same client."""

    client: Client
    databases: Databases
    guild_cogs: Cogs
    guild_settings: GuildSettings
    guild_members: Members
    guild_messages: Messages
    guild_logs: Logs
    guild_logs_files: LogsFiles
    guild_analytics_archives: Archives
    keiko_settings: KeikoSettings
    keiko_integrations: Integrations
    keiko_schedules: Schedules
    keiko_subscriptions: Subscriptions
    events_changes: Changes
    events_activity: Activity
    events_interactions: Interactions
    analytics_counts: Counts
    analytics_keiko: Keiko
    analytics_lifetime: Lifetime
    traces_runs: Runs
    migrations_changelog: Changelog

    async def close(self) -> None:
        """Close the client's connections."""
        await self.client.close()


def build(client: Client) -> Data:
    """Every repository over `client`."""
    guild, keiko, events = client["guild"], client["keiko"], client["events"]
    analytics = client["analytics"]
    return Data(
        client=client,
        databases=Databases(client),
        guild_cogs=Cogs(guild["cogs"]),
        guild_settings=GuildSettings(guild["settings"]),
        guild_members=Members(guild["members"]),
        guild_messages=Messages(guild["messages"]),
        guild_logs=Logs(guild["logs"]),
        guild_logs_files=LogsFiles(guild["logs_files"]),
        guild_analytics_archives=Archives(guild["analytics_archives"]),
        keiko_settings=KeikoSettings(keiko["settings"]),
        keiko_integrations=Integrations(keiko["integrations"]),
        keiko_schedules=Schedules(keiko["schedules"]),
        keiko_subscriptions=Subscriptions(keiko["subscriptions"]),
        events_changes=Changes(events["changes"]),
        events_activity=Activity(events["activity"]),
        events_interactions=Interactions(events["interactions"]),
        analytics_counts=Counts(analytics["counts"]),
        analytics_keiko=Keiko(analytics["keiko"]),
        analytics_lifetime=Lifetime(analytics["lifetime"]),
        traces_runs=Runs(client["traces"]["runs"]),
        migrations_changelog=Changelog(client["migrations"]["changelog"]),
    )


_INSTALLED: Optional[Data] = None
_LOOP: Optional[asyncio.AbstractEventLoop] = None
_LOOP_EXPECTED = threading.Event()
_LOOP_BOUND = threading.Event()
_THREAD = threading.local()


def install(installed: Data) -> None:
    """Make `installed` the data every caller of `data()` reaches."""
    global _INSTALLED
    _INSTALLED = installed


def data() -> Data:
    """The repositories installed at startup."""
    if _INSTALLED is None:
        raise RuntimeError(
            "The data layer is not installed: create_app installs it over the Mongo "
            "client, and the test suite over its fake"
        )
    return _INSTALLED


def expect_loop() -> None:
    """The client will live on a loop bound later: until it is, `wait` waits for it
    rather than run on a loop of its own, which would take the client from the bot."""
    _LOOP_EXPECTED.set()


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """The loop the client lives on, which a worker thread reads through."""
    global _LOOP
    _LOOP = loop
    _LOOP_BOUND.set()


def wait(awaitable: Coroutine[Any, Any, Answer]) -> Answer:
    """From a worker thread, the answer of `awaitable` run on the client's loop, given
    up on with a `TimeoutError` after `DBConfigs.MONGO_THREAD_WAIT_SECONDS`."""
    expected = _LOOP_EXPECTED.is_set()
    if expected and not _LOOP_BOUND.wait(DBConfigs.MONGO_BOOT_WAIT_SECONDS):
        awaitable.close()
        raise RuntimeError("the bot's loop has not started, so Mongo is not reachable")

    loop = _LOOP
    if loop is None or not loop.is_running():
        return _own_loop().run_until_complete(awaitable)
    if _running_loop() is loop:
        awaitable.close()
        raise RuntimeError("wait() on the loop's own thread would never return")
    answer = asyncio.run_coroutine_threadsafe(awaitable, loop)
    try:
        return answer.result(DBConfigs.MONGO_THREAD_WAIT_SECONDS)
    except concurrent.futures.TimeoutError:
        answer.cancel()
        waited = DBConfigs.MONGO_THREAD_WAIT_SECONDS
        message = f"no Mongo answer from the bot's loop in {waited}s"
        raise TimeoutError(message) from None


def iterate(source: AsyncGenerator[Answer, None]) -> Iterator[Answer]:
    """From a worker thread, the items of `source`, read on the client's loop a batch
    at a time, and `source` closed once the reader stops."""
    try:
        while True:
            batch = wait(_next_batch(source, DBConfigs.MONGO_THREAD_BATCH))
            if not batch:
                return
            yield from batch
    finally:
        wait(source.aclose())


async def _next_batch(source: AsyncGenerator[Answer, None], size: int) -> List[Answer]:
    batch: List[Answer] = []
    async for item in source:
        batch.append(item)
        if len(batch) == size:
            break
    return batch


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _own_loop() -> asyncio.AbstractEventLoop:
    loop: Optional[asyncio.AbstractEventLoop] = getattr(_THREAD, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _THREAD.loop = loop
    return loop

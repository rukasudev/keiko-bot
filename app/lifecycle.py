"""How the process runs and stops: the threads blocking calls run on, SIGTERM closing
the bot, and the queues written however it stops."""

import asyncio
import concurrent.futures
import contextlib
import os
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import FrameType
from typing import Any, Callable, Optional, Set

import discord
from discord.ext import commands

from app import logger
from app.constants import DBConfigs, Dependencies, LogTypes
from app.services import analytics, debug_logs
from app.services.trace import trace_scope


class BlockingIO(ThreadPoolExecutor):
    """The threads every blocking call runs on; stopping them waits for none, and the
    process gives the calls under way a short while before it ends."""

    def __init__(self) -> None:
        super().__init__(
            max_workers=Dependencies.BLOCKING_IO_THREADS, thread_name_prefix="keiko-io"
        )
        self._under_way: Set["Future[Any]"] = set()
        self._tracking = threading.Lock()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> "Future[Any]":
        future = super().submit(fn, *args, **kwargs)
        with self._tracking:
            self._under_way.add(future)
        future.add_done_callback(self._settled)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        super().shutdown(wait=False, cancel_futures=True)

    def wait_under_way(self, timeout: float) -> None:
        """Wait up to `timeout` seconds for the calls already running to end."""
        with self._tracking:
            under_way = list(self._under_way)
        concurrent.futures.wait(under_way, timeout=timeout)

    def _settled(self, future: "Future[Any]") -> None:
        with self._tracking:
            self._under_way.discard(future)


_POOL: Optional[BlockingIO] = None


def install_blocking_io() -> None:
    """Make Keiko's pool the running loop's default executor, behind `asyncio.to_thread`."""
    global _POOL
    _POOL = BlockingIO()
    asyncio.get_running_loop().set_default_executor(_POOL)


def run(bot: commands.Bot, token: str) -> None:
    """Run `bot`, write its queues however it stops, and end the process when asked."""
    stop_asked = False

    def close_the_bot(_signal: int, _frame: Optional[FrameType]) -> None:
        nonlocal stop_asked
        if not stop_asked:
            stop_asked = True
            stop(bot)

    def note_the_stop(_signal: int, _frame: Optional[FrameType]) -> None:
        nonlocal stop_asked
        stop_asked = True

    _announce_ready(bot)
    failure: Optional[Exception] = None
    try:
        with contextlib.suppress(KeyboardInterrupt):
            try:
                signal.signal(signal.SIGTERM, close_the_bot)
                bot.run(token, reconnect=True)
            finally:
                signal.signal(signal.SIGTERM, note_the_stop)
    except Exception as error:
        failure = error

    if failure is not None:
        _report(failure, stop_asked)
    _let_writes_under_way_end()
    write_queues()

    signal.signal(signal.SIGTERM, lambda _signal, _frame: os._exit(0))
    if stop_asked:
        os._exit(0)
    if failure is None:
        return

    if _refused_by_discord(failure):
        time.sleep(DBConfigs.FATAL_START_BACKOFF_SECONDS)
        signal.signal(signal.SIGTERM, note_the_stop)
        write_queues()
    os._exit(0 if stop_asked else 1)


def stop(bot: commands.Bot) -> None:
    """Close `bot` on its own loop between two callbacks, or stop like Ctrl+C."""
    loop = bot.loop
    if not isinstance(loop, asyncio.AbstractEventLoop) or loop.is_closed():
        raise KeyboardInterrupt

    asyncio.run_coroutine_threadsafe(bot.close(), loop)


def write_queues() -> None:
    """Write what the analytics and debug log queues still hold."""
    analytics.flush()
    debug_logs.stop_writer()
    debug_logs.flush()


def _let_writes_under_way_end() -> None:
    """A flush still writing its batch in a thread when the loop closed is given
    `Dependencies.BLOCKING_IO_STOP_WAIT_SECONDS` to finish, so the exit never cuts it."""
    if _POOL is not None:
        _POOL.wait_under_way(Dependencies.BLOCKING_IO_STOP_WAIT_SECONDS)


def _announce_ready(bot: commands.Bot) -> None:
    async def ready() -> None:
        bot.remove_listener(ready, "on_ready")
        with trace_scope("ready", quiet=True):
            logger.info(
                DBConfigs.READY_PHRASE, log_type=LogTypes.APPLICATION_STARTUP_TYPE
            )

    bot.add_listener(ready, "on_ready")


def _refused_by_discord(failure: Optional[Exception]) -> bool:
    if isinstance(failure, discord.ConnectionClosed):
        return failure.code in DBConfigs.FATAL_START_CLOSE_CODES
    return isinstance(
        failure, (discord.LoginFailure, discord.PrivilegedIntentsRequired)
    )


def _report(failure: Exception, stop_asked: bool) -> None:
    reason = f"{type(failure).__name__}: {failure}"
    message = f"The bot stopped on {reason}"
    if stop_asked:
        level = logger.logger.warning
    elif _refused_by_discord(failure):
        level = logger.logger.critical
        message = f"{DBConfigs.FATAL_START_PHRASE}: {reason}"
    else:
        level = logger.logger.error

    logger.log(
        level, message, log_type=LogTypes.APPLICATION_ERROR_TYPE, exc_info=failure
    )

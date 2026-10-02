"""How the process stops: SIGTERM closes the bot and writes what it still queues."""

import asyncio
import contextlib
import os
import signal
import time
from types import FrameType
from typing import Optional

import discord
from discord.ext import commands

from app import logger
from app.constants import DBConfigs, LogTypes
from app.services import analytics, debug_logs
from app.services.trace import trace_scope


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

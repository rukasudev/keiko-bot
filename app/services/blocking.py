"""Blocking calls, kept off the event loop.

The bot runs everything on one loop: the gateway heartbeat, every interaction,
every listener, every scheduled job. A synchronous call inside a coroutine does
not slow one command down, it stops the bot — and Discord gives an interaction
three seconds to be acknowledged, so a blocking call between the click and the
acknowledgement is a failed interaction the user sees.

Production made both halves concrete. 52 `heartbeat blocked for more than 20
seconds` warnings in one day, every traceback ending in a `find_one` reached
from `on_message`, followed by two restarts. And a `10062 Unknown interaction`
on a guild that had just subscribed a youtuber: the subscription was saved, two
`requests.post` calls had spent the three seconds, and the person saw "This
interaction failed" and started over.

`requests`, `pymongo` and `time.sleep` are synchronous. Anything that reaches
them from a coroutine goes through here.
"""
import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")


async def off_loop(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Await a blocking function in a worker thread.

    The thread runs in a copy of the current context, so a `logger.*` call
    inside `func` still lands on the trace that scheduled it, and the exception
    it raises surfaces here rather than being swallowed by a thread nobody
    reads.
    """
    return await asyncio.to_thread(func, *args, **kwargs)

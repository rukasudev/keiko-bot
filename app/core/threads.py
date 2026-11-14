"""The threads Keiko's blocking calls run on, apart from the threads the loop keeps for
its own work, so a host name lookup or a TLS handshake never queues behind a call that
waits on the loop."""

import asyncio
import concurrent.futures
import contextvars
import functools
import threading
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from typing import Any, Callable, Optional, Sequence, Set, Tuple, TypeVar

from app.constants import Dependencies

Answer = TypeVar("Answer")


class BlockingIO(ThreadPoolExecutor):
    """A pool of named threads; stopping it stops `stopping` too and waits for none, and
    the process gives the calls under way a short while before it ends."""

    def __init__(self, threads: int, name: str, stopping: Sequence[Executor] = ()) -> None:
        super().__init__(max_workers=threads, thread_name_prefix=name)
        self._stopping = tuple(stopping)
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
        for executor in self._stopping:
            executor.shutdown(wait=False, cancel_futures=True)

    def wait_under_way(self, timeout: float) -> None:
        """Wait up to `timeout` seconds for the calls already running to end."""
        with self._tracking:
            under_way = list(self._under_way)
        concurrent.futures.wait(under_way, timeout=timeout)

    def _settled(self, future: "Future[Any]") -> None:
        with self._tracking:
            self._under_way.discard(future)


_POOL: Optional[Tuple[asyncio.AbstractEventLoop, BlockingIO]] = None


def install() -> None:
    """Run the bot's blocking calls on Keiko's pool, and the running loop's own work on
    a small pool of its own that closing the loop stops with it."""
    global _POOL
    loop = asyncio.get_running_loop()
    pool = BlockingIO(Dependencies.BLOCKING_IO_THREADS, "keiko-io")
    loop.set_default_executor(
        BlockingIO(Dependencies.LOOP_IO_THREADS, "keiko-loop", stopping=(pool,))
    )
    _POOL = (loop, pool)


async def to_thread(fn: Callable[..., Answer], /, *args: Any, **kwargs: Any) -> Answer:
    """`fn(*args, **kwargs)` on Keiko's pool, or on the loop's default executor where no
    pool was installed for the running loop."""
    loop = asyncio.get_running_loop()
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return await loop.run_in_executor(_pool_of(loop), call)


def wait_under_way(timeout: float) -> None:
    """Wait up to `timeout` seconds for the calls running on Keiko's pool to end."""
    if _POOL is not None:
        _POOL[1].wait_under_way(timeout)


async def under_way_ended(timeout: float) -> None:
    """Wait off the loop, up to `timeout` seconds, for the calls running on Keiko's pool
    to end."""
    await asyncio.get_running_loop().run_in_executor(None, wait_under_way, timeout)


def _pool_of(loop: asyncio.AbstractEventLoop) -> Optional[BlockingIO]:
    if _POOL is None or _POOL[0] is not loop:
        return None
    return _POOL[1]

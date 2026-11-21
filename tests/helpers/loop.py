"""How a test tells the event loop kept running: count its ticks while a call ran.

`blocking` stands in for a synchronous call (`requests`, `time.sleep`, pymongo) and
`slow` for an awaited one of the same length, so a test can tell a coroutine that
froze the loop from one that let it run.
"""
import asyncio
import time

BLOCKING_SECONDS = 0.25
TICK_SECONDS = 0.005
# A loop that never yields ticks zero or one time; a healthy one ticks ~50.
MIN_TICKS = 5


async def ticks_while(awaitable):
    """How many times the loop got control back while `awaitable` ran."""
    ticks = 0
    running = True

    async def heartbeat():
        nonlocal ticks
        while running:
            await asyncio.sleep(TICK_SECONDS)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    try:
        await awaitable
    finally:
        running = False
        beat.cancel()

    return ticks


def blocking(return_value=None):
    """A stand-in for `requests` or `time.sleep`."""
    def call(*args, **kwargs):
        time.sleep(BLOCKING_SECONDS)
        return return_value
    return call


def slow(return_value=None):
    """A stand-in for a repository method on a slow Mongo: it takes as long as
    `blocking`, awaited, so the loop keeps running meanwhile."""
    async def call(*args, **kwargs):
        await asyncio.sleep(BLOCKING_SECONDS)
        return return_value
    return call


def blocking_rows(rows):
    """A stand-in for a streamed read a thread job makes, each call holding its thread."""
    async def stream(*args, **kwargs):
        time.sleep(BLOCKING_SECONDS)
        for row in rows:
            yield row
    return stream

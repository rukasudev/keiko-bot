"""Locks keyed by what they guard, each kept while a holder or a waiter needs it."""

import asyncio
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager


class KeyedLocks:
    """One lock per key, so the work on one key runs one at a time and the work on
    different keys runs side by side."""

    def __init__(self) -> None:
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._needed: dict[Hashable, int] = {}

    @asynccontextmanager
    async def hold(self, key: Hashable) -> AsyncIterator[None]:
        """Hold the lock of `key` for the block, after whoever holds it now."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._needed[key] = self._needed.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._needed[key] -= 1
            if not self._needed[key]:
                del self._needed[key]
                del self._locks[key]

    def __len__(self) -> int:
        """How many keys a holder or a waiter needs now."""
        return len(self._locks)

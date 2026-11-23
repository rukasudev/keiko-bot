"""Keyed locks: the work on one key runs one at a time, the work on different keys
side by side, and a key nobody needs any more holds no lock."""

import asyncio

import pytest

from app.core.locks import KeyedLocks

pytestmark = pytest.mark.unit


async def test_one_key_runs_one_at_a_time_and_two_keys_side_by_side() -> None:
    locks = KeyedLocks()
    order: list[str] = []

    async def work(key: str, name: str) -> None:
        async with locks.hold(key):
            order.append(f"{name} in")
            await asyncio.sleep(0)
            order.append(f"{name} out")

    await asyncio.gather(
        work("ana", "first"), work("ana", "second"), work("bia", "other")
    )

    assert order.index("first out") < order.index("second in")
    assert order.index("other in") < order.index("first out")
    assert len(locks) == 0


async def test_a_failed_holder_lets_the_next_one_in() -> None:
    locks = KeyedLocks()

    with pytest.raises(RuntimeError):
        async with locks.hold("ana"):
            raise RuntimeError("the role could not be given")
    async with locks.hold("ana"):
        held = len(locks)

    assert (held, len(locks)) == (1, 0)

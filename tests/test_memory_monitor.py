"""The memory monitor publishes what holds the bot's memory, without touching Discord."""
import tracemalloc
from types import SimpleNamespace

import pytest

from app.services import memory_monitor


def _value(gauge, **labels):
    return gauge.labels(**labels)._value.get()


def _bot():
    guilds = [SimpleNamespace(members=[1, 2, 3]), SimpleNamespace(members=[4])]
    store = SimpleNamespace(_views={1: {}, 2: {}}, _synced_message_views={1: object()})
    return SimpleNamespace(
        guilds=guilds,
        cached_messages=[object()] * 5,
        persistent_views=[object()],
        _connection=SimpleNamespace(_view_store=store),
    )


async def test_a_sample_publishes_the_size_of_every_structure_that_can_grow():
    memory_monitor.sample(_bot())

    assert _value(memory_monitor.HELD, structure="guilds") == 2
    assert _value(memory_monitor.HELD, structure="members") == 4
    assert _value(memory_monitor.HELD, structure="cached_messages") == 5
    assert _value(memory_monitor.HELD, structure="view_messages") == 2
    assert _value(memory_monitor.HELD, structure="synced_views") == 1
    assert _value(memory_monitor.HELD, structure="welcome_images") >= 0


def test_a_census_names_the_most_common_object_types():
    memory_monitor.census()

    types = {sample.labels["type"] for sample in memory_monitor.OBJECTS.collect()[0].samples}
    assert "dict" in types
    assert len(types) <= memory_monitor.constants.TOP_TYPES


def test_tracing_stays_off_unless_the_environment_asks(monkeypatch):
    monkeypatch.delenv(memory_monitor.constants.TRACEMALLOC_ENV, raising=False)
    was_tracing = tracemalloc.is_tracing()

    assert memory_monitor.start_tracing() is was_tracing
    assert memory_monitor.traced_lines() == [] or was_tracing


def test_tracing_reports_the_lines_holding_memory_when_asked(monkeypatch):
    if tracemalloc.is_tracing():
        pytest.skip("tracemalloc already on in this run")
    monkeypatch.setenv(memory_monitor.constants.TRACEMALLOC_ENV, "1")
    try:
        assert memory_monitor.start_tracing() is True
        kept = [bytearray(1024) for _ in range(100)]
        lines = memory_monitor.traced_lines()
        assert lines and all(":" in location for location, _ in lines)
        del kept
    finally:
        tracemalloc.stop()

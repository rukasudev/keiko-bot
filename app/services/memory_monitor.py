"""What holds the bot's memory, as Prometheus gauges read over time."""
import asyncio
import gc
import os
import tracemalloc
from collections import Counter
from typing import Callable, Dict, List, Tuple

from prometheus_client import Gauge

from app.constants import MemoryMonitor as constants

HELD = Gauge(
    "keiko_memory_held_entries",
    "How many entries each cache or Discord collection holds",
    ["structure"],
)
OBJECTS = Gauge(
    "keiko_memory_objects",
    "Live Python objects of the most common types at the last census",
    ["type"],
)
TRACED = Gauge(
    "keiko_memory_traced_bytes",
    "Bytes still allocated by each source line, when tracemalloc is on",
    ["location"],
)


def start_tracing() -> bool:
    """Turn tracemalloc on when the environment asks for it."""
    frames = os.getenv(constants.TRACEMALLOC_ENV)
    if not frames or tracemalloc.is_tracing():
        return tracemalloc.is_tracing()
    tracemalloc.start(int(frames))
    return True


def held_entries(bot) -> Dict[str, int]:
    """The size of every structure that can grow, read without walking the heap."""
    from app.services import utils, welcome_messages

    connection = bot._connection
    return {
        "guilds": len(bot.guilds),
        "members": sum(len(guild.members) for guild in bot.guilds),
        "cached_messages": len(bot.cached_messages),
        "view_messages": len(connection._view_store._views),
        "synced_views": len(connection._view_store._synced_message_views),
        "persistent_views": len(bot.persistent_views),
        "asyncio_tasks": len(asyncio.all_tasks()),
        "welcome_images": welcome_messages.request_image_url.cache_info().currsize,
        "form_yamls": utils.parse_form_yaml_to_dict.cache_info().currsize,
        "step_titles": utils.step_titles.cache_info().currsize,
    }


def object_census(top: int = constants.TOP_TYPES) -> List[Tuple[str, int]]:
    """The most common live object types, by count."""
    counts = Counter(type(item).__qualname__ for item in gc.get_objects())
    return counts.most_common(top)


def traced_lines(top: int = constants.TOP_LINES) -> List[Tuple[str, int]]:
    """The source lines holding the most traced memory, when tracing is on."""
    if not tracemalloc.is_tracing():
        return []
    stats = tracemalloc.take_snapshot().statistics("lineno")[:top]
    return [(f"{stat.traceback[0].filename}:{stat.traceback[0].lineno}", stat.size) for stat in stats]


def publish(gauge: Gauge, rows, label: Callable[[str], Dict[str, str]]) -> None:
    for key, value in rows:
        gauge.labels(**label(key)).set(value)


def sample(bot) -> None:
    """Read the cheap sizes; they run every few minutes."""
    publish(HELD, held_entries(bot).items(), lambda key: {"structure": key})


def census() -> None:
    """Walk the heap; runs in a thread, every half hour."""
    OBJECTS.clear()
    publish(OBJECTS, object_census(), lambda key: {"type": key})


def trace() -> None:
    """Read the tracer's snapshot; runs in a thread, every two hours."""
    TRACED.clear()
    publish(TRACED, traced_lines(), lambda key: {"location": key})

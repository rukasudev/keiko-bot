"""The canonical admin paths of every form, one registry.

Each path is an async driver `run(scenario_factory, deps, locale) -> FormScenario`
registered with `@golden_path(form, name, locales)`. The golden test module
records or checks `tests/behavioral/golden/<form>/<name>.<locale>.json` for
every (path, locale) pair. Drivers speak only the harness API, never the
engine, so the same path runs on the old and the new engine.
"""
import importlib
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Tuple

FORMS = (
    "default_roles",
    "stream_elements_commands",
    "welcome_messages",
    "block_links",
    "notifications_twitch",
    "notifications_youtube_video",
    "reminders_birthday",
)


@dataclass(frozen=True)
class GoldenPath:
    form: str
    name: str
    locales: Tuple[str, ...]
    run: Callable[..., Awaitable]

    @property
    def id(self) -> str:
        return f"{self.form}/{self.name}"


REGISTRY: List[GoldenPath] = []


def golden_path(form: str, name: str, locales=("pt-br",)):
    def register(run):
        REGISTRY.append(GoldenPath(form, name, tuple(locales), run))
        return run

    return register


def all_paths() -> List[GoldenPath]:
    for form in FORMS:
        importlib.import_module(f"{__name__}.{form}")
    return list(REGISTRY)

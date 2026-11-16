"""The canonical admin paths of every form, one registry.

Each path is an async driver `run(scenario_factory, deps, locale) -> FormScenario`
registered with `@golden_path(form, name, locales)` in the module named after its
form, which `all_paths` finds through the registry. The golden test module
records or checks `tests/behavioral/golden/<form>/<name>.<locale>.json` for
every (path, locale) pair. Drivers speak only the harness API, never the
engine, so the same path runs on the old and the new engine.
"""
import importlib
import importlib.util
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Tuple

from app.core.commands import commands
from app.forms.form.form_yaml import DiskSource


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
    """Every path of the modules named after a declared form, each form's own."""
    forms = set(DiskSource().list())
    for command in commands():
        module = f"{__name__}.{command.key}"
        if command.key in forms and importlib.util.find_spec(module) is not None:
            importlib.import_module(module)
    return list(REGISTRY)

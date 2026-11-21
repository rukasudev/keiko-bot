"""The canonical admin paths of every form, one registry.

Each path is an async driver `run(scenario_factory, deps, locale) -> FormScenario`
registered with `@golden_path(form, name, locales)` in the module named after its
form, which `all_paths` finds through the registry. The same module declares
the form's book with `golden_book(FORM, rendered=(<panel>, <review>))`, and the
golden test module records or checks the flow `<name>.<locale>` of every
(path, locale) pair in that one book, `tests/behavioral/golden/<form>.yml`.
Drivers speak only the harness API, never the engine, so the same path runs on
the old and the new engine.
"""
import importlib
import importlib.util
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, FrozenSet, Iterable, List, Tuple

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


@dataclass(frozen=True)
class GoldenBook:
    form: str
    rendered: Tuple[str, ...]


REGISTRY: List[GoldenPath] = []
BOOKS: Dict[str, GoldenBook] = {}


def golden_book(form: str, rendered: Iterable[str] = ()) -> None:
    """Record every path of `form` in one book, keeping the words of the
    `rendered` screens beside their keys."""
    BOOKS[form] = GoldenBook(form, tuple(rendered))


def flow_ids(form: str) -> FrozenSet[str]:
    """The flow of every (path, locale) pair `form` registers."""
    return frozenset(
        f"{path.name}.{locale}"
        for path in REGISTRY
        if path.form == form
        for locale in path.locales
    )


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

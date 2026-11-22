"""What the tests of the shared modules share: a registry of their own."""

import sys
from collections.abc import Callable, Iterator
from types import ModuleType

import pytest

from app.core.commands import Command, commands


@pytest.fixture
def declare(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Install `COMMANDS` as the list `app.commands` declares."""

    def install(*declared: Command) -> None:
        module = ModuleType("app.commands")
        module.COMMANDS = list(declared)
        monkeypatch.setitem(sys.modules, "app.commands", module)
        commands.cache_clear()

    yield install
    commands.cache_clear()

"""Where raw definitions come from: disk today, a database draft tomorrow."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

import yaml


class DefinitionSource(Protocol):
    """A place raw form definitions are read from."""

    def load(self, key: str) -> Mapping[str, Any]:
        """The raw definition of the form `key`."""

    def list(self) -> tuple[str, ...]:
        """Every form key the source knows."""


class DiskSource:
    """The YAML files under `app/languages/form/`."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("app") / "languages" / "form"

    def load(self, key: str) -> Mapping[str, Any]:
        """The parsed YAML of `<root>/<key>.yml`."""
        path = self.root / f"{key.lower()}.yml"
        with path.open(encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, Mapping):
            raise ValueError(f"{path} is not a mapping")
        return loaded

    def list(self) -> tuple[str, ...]:
        """Every form key with a file under the root."""
        return tuple(sorted(path.stem for path in self.root.glob("*.yml")))

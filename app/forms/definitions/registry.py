"""Every compiled definition, keyed by form and version, loaded once at boot."""

from __future__ import annotations

from app.forms.definitions.compiler import compile_with_warnings
from app.forms.definitions.schema import FormDefinition
from app.forms.definitions.source import DefinitionSource, DiskSource


class DefinitionRegistry:
    """The compiled forms of one source."""

    def __init__(self, source: DefinitionSource | None = None) -> None:
        self.source: DefinitionSource = source or DiskSource()
        self._definitions: dict[tuple[str, int], FormDefinition] = {}
        self._latest: dict[str, int] = {}
        self.warnings: tuple[str, ...] = ()

    def load_all(self) -> tuple[FormDefinition, ...]:
        """Compile every form the source lists; a bad one raises `CompileError`."""
        warnings: list[str] = []
        for key in self.source.list():
            compiled = compile_with_warnings(key, self.source.load(key))
            self._register(compiled.definition)
            warnings.extend(compiled.warnings)
        self.warnings = tuple(warnings)
        return tuple(self._definitions.values())

    def _register(self, definition: FormDefinition) -> None:
        self._definitions[(definition.key, definition.version)] = definition
        self._latest[definition.key] = max(
            definition.version, self._latest.get(definition.key, 0)
        )

    def get(self, key: str, version: int | None = None) -> FormDefinition:
        """The definition of `key` at `version`, the latest when not given."""
        if key not in self._latest:
            self._register(compile_with_warnings(key, self.source.load(key)).definition)
        return self._definitions[(key, version or self._latest[key])]

    def keys(self) -> tuple[str, ...]:
        """Every form key loaded so far."""
        return tuple(sorted(self._latest))


registry = DefinitionRegistry()

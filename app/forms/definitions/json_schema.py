"""The JSON Schema of a compiled definition, for builders and generators."""

from __future__ import annotations

from typing import Any

from app.forms.definitions.schema import FormDefinition


def export() -> dict[str, Any]:
    """The schema every compiled form satisfies, with the copy in both locales."""
    return FormDefinition.model_json_schema(by_alias=True)

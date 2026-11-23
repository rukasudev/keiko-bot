"""Native channel and role selects: what a card's selects section draws and keeps."""

from __future__ import annotations

from typing import Any

from app.forms.form.components import Picker
from app.forms.form.form_yaml import Select

SELECT_KIND = {"channels": "channel", "roles": "role", "assignable_roles": "role"}


def picked_ids(value: Any) -> tuple[str, ...]:
    """The ids a stored pick holds: none, one or several."""
    if value in (None, "", []):
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(value) for value in value)
    return (str(value),)


def select_picker(
    step_key: str, select: Select, picked: Any, locale: str, required: bool = False
) -> Picker:
    """The native select a declared `Select` draws, with what it holds picked."""
    return Picker(
        step_key=step_key,
        kind=SELECT_KIND[select.type],  # type: ignore[arg-type]
        placeholder=select.placeholder.get(locale),
        selected=picked_ids(picked),
        max_values=select.max,
        slot=select.key,
        available_only=select.type == "assignable_roles",
        warns_powerful=select.powerful_roles_warning is not None,
        required=required,
    )


def stored_ids(values: tuple[str, ...]) -> Any:
    """The ids as a document keeps them: one as itself, several as a list."""
    return values[0] if len(values) == 1 else list(values)

"""One module per step type: `render` a screen, `parse` a payload into answers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.forms.form.actions.action import Context
from app.forms.form.components import Screen
from app.forms.form.form_state import Answer, FormSession


@dataclass(frozen=True)
class Refusal:
    """A payload the step could not accept, with the error copy key."""

    error_key: str
    args: Mapping[str, str] | None = None
    plain: bool = False
    delete_after: int | None = 10
    inputs: tuple[str, ...] = ()
    target: str = ""
    missing: tuple[str, ...] = ()
    warning: bool = False


Render = Callable[["Any", FormSession, Context], Screen]
Parse = Callable[["Any", Any, FormSession, Context], "Mapping[str, Answer] | Refusal"]


@dataclass(frozen=True)
class Kind:
    """The two functions a step type contributes."""

    render: Render
    parse: Parse


def registry() -> dict[str, Kind]:
    """Every step type by name."""
    from app.forms.form.actions import (
        composition,
        configuration_card,
        intro,
        modal,
        options,
        resume,
    )

    return {
        "intro": Kind(intro.render, intro.parse),
        "text": Kind(modal.render, modal.parse),
        "single_choice": Kind(options.render, options.parse),
        "card": Kind(configuration_card.render, configuration_card.parse),
        "list": Kind(composition.render, composition.parse),
        "review": Kind(resume.render, resume.parse),
    }

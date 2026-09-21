"""One module per step kind: `render` a screen, `parse` a payload into answers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.settings.form.actions.action import RenderContext
from app.settings.form.components import Screen
from app.settings.form.form_state import Answer, FormSession


@dataclass(frozen=True)
class Refusal:
    """A payload the step could not accept, with the error copy key."""

    error_key: str
    args: Mapping[str, str] | None = None
    plain: bool = False
    delete_after: int | None = 10


Render = Callable[["Any", FormSession, RenderContext], Screen]
Parse = Callable[
    ["Any", Any, FormSession, RenderContext], "Mapping[str, Answer] | Refusal"
]


@dataclass(frozen=True)
class Kind:
    """The two functions a step kind contributes."""

    render: Render
    parse: Parse


def registry() -> dict[str, Kind]:
    """Every step kind by name."""
    from app.settings.form.actions import (
        composition,
        configuration_card,
        info,
        intro,
        modal,
        options,
        resume,
        selects,
    )

    return {
        "intro": Kind(intro.render, intro.parse),
        "text": Kind(modal.render, modal.parse),
        "single_choice": Kind(options.render, options.parse),
        "channel_pick": Kind(selects.render, selects.parse),
        "role_pick": Kind(selects.render, selects.parse),
        "user_pick": Kind(selects.render, selects.parse),
        "multi_pick": Kind(selects.render, selects.parse),
        "card": Kind(configuration_card.render, configuration_card.parse),
        "composition": Kind(composition.render, composition.parse),
        "info": Kind(info.render, info.parse),
        "review": Kind(resume.render, resume.parse),
    }

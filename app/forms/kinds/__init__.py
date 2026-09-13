"""One module per step kind: `render` a screen, `parse` a payload into answers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.forms.engine.screen import Screen
from app.forms.engine.session import Answer, FormSession
from app.forms.kinds.context import RenderContext


@dataclass(frozen=True)
class Refusal:
    """A payload the step could not accept, with the error copy key."""

    error_key: str
    args: Mapping[str, str] | None = None
    plain: bool = False


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
    from app.forms.kinds import (
        card,
        composition,
        info,
        intro,
        picks,
        review,
        single_choice,
        text,
    )

    return {
        "intro": Kind(intro.render, intro.parse),
        "text": Kind(text.render, text.parse),
        "single_choice": Kind(single_choice.render, single_choice.parse),
        "channel_pick": Kind(picks.render, picks.parse),
        "role_pick": Kind(picks.render, picks.parse),
        "user_pick": Kind(picks.render, picks.parse),
        "multi_pick": Kind(picks.render, picks.parse),
        "card": Kind(card.render, card.parse),
        "composition": Kind(composition.render, composition.parse),
        "info": Kind(info.render, info.parse),
        "review": Kind(review.render, review.parse),
    }

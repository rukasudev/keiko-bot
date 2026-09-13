"""A list of items, each built by a child session; the step itself has no screen."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.forms.engine.screen import Screen
from app.forms.engine.session import Answer, FormSession
from app.forms.kinds.context import RenderContext


def render(step: Any, session: FormSession, context: RenderContext) -> Screen:
    """A composition renders through its child session, never directly."""
    raise NotImplementedError("a composition opens a child session")


def parse(
    step: Any, payload: Any, session: FormSession, context: RenderContext
) -> Mapping[str, Answer]:
    """A composition is answered by its child sessions, never by a payload."""
    return {}


def items_of(session: FormSession, key: str) -> tuple[Mapping[str, Answer], ...]:
    """The items answered under the composition `key`, possibly none."""
    answer = session.answers.get(key)
    return tuple(answer.raw) if answer and answer.raw else ()

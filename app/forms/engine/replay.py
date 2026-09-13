"""Replay a list of events through `decide`: a session as a unit test."""

from __future__ import annotations

from collections.abc import Sequence

from app.forms.definitions.schema import FormDefinition
from app.forms.engine.decide import Context, Decision, decide
from app.forms.engine.events import Event
from app.forms.engine.session import FormSession


def replay(
    definition: FormDefinition,
    session: FormSession,
    events: Sequence[Event],
    context: Context | None = None,
) -> tuple[Decision, ...]:
    """Every decision the events produce in order, each from the previous session."""
    decisions: list[Decision] = []
    current = session
    for event in events:
        decision = decide(definition, current, event, context)
        decisions.append(decision)
        current = decision.session
    return tuple(decisions)

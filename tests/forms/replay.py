"""Replay a list of events through `decide`: a session as a unit test."""

from __future__ import annotations

from collections.abc import Sequence

from app.settings.form.events import Event
from app.settings.form.form import Context, Decision, decide
from app.settings.form.form_state import FormSession
from app.settings.form.form_yaml import FormDefinition


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

"""Drive sessions in unit tests: events, the decision each one gets, what it shows.

`decided` routes a session the way the adapter does: the manager decides a
manage session, the form engine every other one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from app.settings.form import events as ev
from app.settings.form import manager
from app.settings.form.actions.action import Context
from app.settings.form.effects import OpenModal, Render
from app.settings.form.events import Event
from app.settings.form.form import Decision, decide
from app.settings.form.form_state import FormSession, Manage, Origin, Setup, new_session
from app.settings.form.form_yaml import DefinitionRegistry, FormDefinition
from app.settings.form.manager import PanelExtras

REGISTRY = DefinitionRegistry()
ORIGIN = Origin(guild_id="123456789", user_id="555", locale="pt-br")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
COUNTER = {"count": 0}


def evt(event_type, **fields):
    COUNTER["count"] += 1
    return event_type(event_id=f"e{COUNTER['count']}", **fields)


def decided(
    definition: FormDefinition,
    session: FormSession,
    event: Event,
    context: Context | None = None,
    panel: PanelExtras | None = None,
) -> Decision:
    if isinstance(session.mode, Manage):
        return manager.decide(definition, session, event, context, panel)
    return decide(definition, session, event, context)


def start(form, mode=Setup(), answers=None, context=None, parent_id=None, panel=None):
    definition = REGISTRY.get(form)
    session = new_session(
        (definition.key, definition.version),
        mode,
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers=answers,
        parent_id=parent_id,
    )
    return definition, decided(definition, session, evt(ev.Started), context, panel)


def kinds(decision):
    return [type(effect).__name__ for effect in decision.effects]


def screen(decision):
    for effect in decision.effects:
        if isinstance(effect, (Render, OpenModal)):
            return effect.screen
    raise AssertionError(f"no screen in {kinds(decision)}")


def open_part(definition, parent, context, target):
    opened = decided(definition, parent, evt(ev.EditRequested, target=target), context)
    child = opened.effects[0].session
    return opened, decided(definition, child, evt(ev.Started), context)


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
        decision = decided(definition, current, event, context)
        decisions.append(decision)
        current = decision.session
    return tuple(decisions)

"""The twelve invariants of the review (section 5), one test each.

I10 and I12 are enforced by tests/forms/test_boundary.py; I11 by the
adapter's executor test. The rest live here, on the real definitions.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.forms.definitions.compiler import CompileError, compile_form
from app.forms.definitions.registry import DefinitionRegistry
from app.forms.engine import events as ev
from app.forms.engine.decide import decide
from app.forms.engine.replay import replay
from app.forms.engine.rules import Scope
from app.forms.engine.session import Origin, Setup, Status, new_session
from app.forms.engine.store import InMemorySessionStore, StaleWrite
from app.forms.kinds import registry as kinds
from app.forms.kinds.context import RenderContext

pytestmark = pytest.mark.unit

REGISTRY = DefinitionRegistry()
ORIGIN = Origin("1", "2", "pt-br")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fresh(form="block_links"):
    definition = REGISTRY.get(form)
    return definition, new_session(
        (definition.key, definition.version), Setup(), ORIGIN, ttl_seconds=60, now=NOW
    )


def happy_path_events():
    return [
        ev.Started("s"),
        ev.Answered("a", None, "form"),
        ev.Answered("b", None, "link_settings"),
        ev.Answered("c", None, "add_custom", False),
        ev.Answered("d", None, "permissions"),
        ev.Answered("e", None, "confirm"),
        ev.CommitSucceeded("f", None, "setup"),
    ]


def test_i1_one_session_one_id_and_a_strictly_increasing_revision():
    definition, session = fresh()
    revisions = [session.revision]
    for decision in replay(definition, session, happy_path_events()):
        assert decision.session.id == session.id
        revisions.append(decision.session.revision)
    assert revisions == sorted(revisions) and len(set(revisions)) == len(revisions)


def test_i2_only_decide_changes_a_session_the_store_only_replaces_it():
    definition, session = fresh()
    with pytest.raises(FrozenInstanceError):
        session.status = Status.COMPLETED  # type: ignore[misc]
    store = InMemorySessionStore()
    store.create(session)
    with pytest.raises(StaleWrite):
        store.put(session)
    decision = decide(definition, session, ev.Started("s"))
    store.put(decision.session)
    assert store.get(session.id) is decision.session


def test_i3_a_closed_session_rejects_every_state_changing_event():
    definition, session = fresh()
    closed = replay(definition, session, happy_path_events())[-1].session
    assert closed.status is Status.COMPLETED
    for event in (ev.Answered("x", None, "confirm"), ev.Back("y"), ev.Cancel("z")):
        decision = decide(definition, closed, event)
        assert decision.rejected == "closed" and decision.session is closed


def test_i4_the_same_event_id_is_applied_once():
    definition, session = fresh()
    first = decide(definition, session, ev.Started("same"))
    second = decide(definition, first.session, ev.Started("same"))
    assert second.rejected == "duplicate" and second.session is first.session


def test_i5_an_older_expected_revision_is_rejected():
    definition, session = fresh()
    started = decide(definition, session, ev.Started("s"))
    older = started.session.screen_revision - 1
    decision = decide(definition, started.session, ev.Answered("a", older, "form"))
    assert decision.rejected == "stale"


def test_i6_render_is_a_pure_function_of_definition_and_session():
    definition, session = fresh()
    started = decide(definition, session, ev.Started("s")).session
    step = definition.steps[0]
    context = RenderContext(definition, definition.steps, "pt-br", Scope({}))
    assert kinds()["intro"].render(step, started, context) == kinds()["intro"].render(
        step, started, context
    )
    twice = [
        decide(definition, started, ev.Answered("a", None, "form")) for _ in range(2)
    ]
    assert twice[0].effects == twice[1].effects


def test_i7_a_decision_hands_features_answers_never_the_session():
    from app.forms.engine.effects import Commit

    definition, session = fresh()
    committing = replay(definition, session, happy_path_events()[:-1])[-1]
    commit = next(e for e in committing.effects if isinstance(e, Commit))
    assert set(commit.payload) == {"answers"}
    with pytest.raises(TypeError):
        commit.payload["answers"]["mode"] = None  # type: ignore[index]


def test_i8_an_invalid_definition_never_compiles():
    with pytest.raises(CompileError):
        compile_form(
            "broken", {"steps": [{"action": "modal", "key": "x", "colour": "red"}]}
        )


def test_i9_every_session_names_its_definition_version():
    definition, session = fresh()
    assert session.definition == (definition.key, definition.version)
    assert REGISTRY.get(*session.definition) is definition


@given(st.lists(st.sampled_from(["a", "b", "c"]), min_size=1, max_size=12))
def test_i4_property_a_replayed_event_id_never_moves_the_session(ids):
    definition, session = fresh()
    current = decide(definition, session, ev.Started("s")).session
    seen: dict[str, int] = {}
    for event_id in ids:
        decision = decide(
            definition, current, ev.Answered(event_id, None, current.cursor or "")
        )
        if event_id in seen:
            assert decision.rejected == "duplicate"
            assert decision.session.revision == current.revision
        else:
            seen[event_id] = decision.session.revision
        current = decision.session


def test_i6_locale_is_normalized_once_at_the_origin():
    from app.forms.extensions.copy import normalize_locale

    assert normalize_locale("en-gb") == "en-us"
    assert normalize_locale("pt-PT") == "pt-br"
    assert normalize_locale("en-US") == "en-us"
    assert normalize_locale("de") == "en-us"

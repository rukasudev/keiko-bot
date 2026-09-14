"""A session only changes through its methods, and each change is a revision."""

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.forms.engine.session import (
    Answer,
    FormSession,
    Origin,
    Setup,
    Status,
    new_session,
)
from app.forms.engine.store import InMemorySessionStore, StaleWrite

pytestmark = pytest.mark.unit

ORIGIN = Origin(guild_id="1", user_id="2", locale="pt-br")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fresh(**changes) -> FormSession:
    return new_session(
        ("block_links", 1), Setup(), ORIGIN, ttl_seconds=60, now=NOW, **changes
    )


def test_a_fresh_session_is_active_at_revision_zero_with_a_deadline():
    session = fresh()
    assert session.status is Status.ACTIVE
    assert session.revision == 0
    assert session.cursor is None
    assert session.expires_at == NOW + timedelta(seconds=60)


def test_a_session_cannot_be_mutated_in_place():
    session = fresh()
    with pytest.raises(AttributeError):
        session.cursor = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        session.answers["x"] = Answer(1)  # type: ignore[index]


def test_answering_replaces_and_never_duplicates():
    session = fresh().with_answer("mode", Answer("a")).with_answer("mode", Answer("b"))
    assert list(session.answers) == ["mode"]
    assert session.raw("mode") == "b"
    assert session.revision == 2


def test_forgetting_answers_keeps_the_others():
    session = fresh().with_answers({"a": Answer(1), "b": Answer(2)}).without(("a",))
    assert session.values() == {"b": 2}


def test_remember_keeps_the_last_events_only():
    session = fresh()
    for index in range(40):
        session = session.remember(f"e{index}")
    assert session.has_seen("e39") and not session.has_seen("e0")
    assert len(session.seen_events) == 32


def test_answer_parts_are_read_only():
    answer = Answer("05-12", {"month": "05", "day": "12"})
    assert answer.part("day") == "12"
    with pytest.raises(TypeError):
        answer.parts["day"] = "1"  # type: ignore[index]


@given(
    st.lists(st.sampled_from(["answer", "status", "cursor", "remember"]), max_size=30)
)
def test_every_state_change_bumps_the_revision_by_exactly_one(steps):
    session = fresh()
    for step in steps:
        before = session.revision
        if step == "answer":
            session = session.with_answer("k", Answer(1))
        elif step == "status":
            session = session.with_status(Status.AWAITING)
        elif step == "cursor":
            session = session.at("k")
        else:
            session = session.remember("e")
            assert session.revision == before, "remembering an event is bookkeeping"
            continue
        assert session.revision == before + 1


def test_the_store_refuses_a_revision_that_did_not_move():
    store = InMemorySessionStore()
    session = fresh()
    store.create(session)
    store.put(session.at("x"))
    with pytest.raises(StaleWrite):
        store.put(session.at("y"))
    assert store.get(session.id).cursor == "x"


def test_the_store_expires_open_sessions_past_their_deadline():
    store = InMemorySessionStore()
    open_session, done = fresh(), fresh().with_status(Status.COMPLETED)
    store.create(open_session)
    store.create(done)
    expired = store.expire(now=NOW + timedelta(seconds=61))
    assert [s.id for s in expired] == [open_session.id]
    assert store.get(open_session.id).status is Status.EXPIRED
    assert store.get(done.id).status is Status.COMPLETED
    assert len(store.forget_closed()) == 2 and len(store) == 0


def test_children_are_found_by_parent():
    store = InMemorySessionStore()
    parent = fresh()
    child = fresh(parent_id=parent.id)
    store.create(parent)
    store.create(child)
    assert store.children(parent.id) == (child,)

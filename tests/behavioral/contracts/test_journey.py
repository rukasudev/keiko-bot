"""One configuration session is one Discord message, edited until it ends.

Before this, the log showed the invocation and nothing else: the steps, the
rejected value and the abandonment all happened inside button clicks, which
open no trace. This suite pins the message that replaced that.
"""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.constants import Commands as constants
from app.services import analytics, journey
from app.services.trace import Trace, trace_scope

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("journey")]

SESSION = "sess01"


@pytest.fixture(autouse=True)
def journey_isolation():
    journey.clear()
    journey.set_publisher(None)
    yield
    journey.clear()
    journey.set_publisher(None)


@pytest.fixture
def published():
    """Captures every render the publisher was asked to do."""
    renders = []
    journey.set_publisher(lambda story: renders.append({
        "id": story.id,
        "result": story.result,
        "lines": [line["message"] for line in story.lines],
        "finished": bool(story.finished_at),
    }))
    return renders


def lines_now(session=SESSION):
    """The story as it stands, published or not."""
    story = journey.get(session)
    return [line["message"] for line in story.lines] if story else []


def start(**kwargs):
    return journey.open_journey(
        SESSION, "moderations block links",
        guild_id="1", user_id="9", feature="block_links", source="slash",
        **kwargs,
    )


def event(name, session=SESSION, **props):
    return {
        "event": name, "session_id": session, "guild_id": "1", "user_id": "9",
        "feature": "block_links", "source": "slash", "props": props, "ts": None,
    }


def test_opening_twice_reuses_the_same_story(published):
    first = start()
    second = start()

    assert first is second, "a sub-form shares the session and must not open a second message"


def test_the_session_starts_as_in_progress(published):
    start()
    assert published[-1]["result"] == "in progress"
    assert published[-1]["finished"] is False


def test_events_of_the_session_become_lines_in_order(published):
    start()
    journey.record(event("feature.setup_opened"))
    journey.record(event("setup.step_viewed", step_key="mode", step_action="options"))
    journey.record(event("setup.step_viewed", step_key="permissions", step_action="roles"))

    assert lines_now() == [
        "setup opened",
        "step: Blocking mode (options)",
        "step: Permissions (roles)",
    ], "step keys must render as the title the user actually saw"


def test_events_of_another_session_are_ignored(published):
    start()
    journey.record(event("setup.step_viewed", session="other", step_key="x", step_action="y"))

    assert lines_now() == []


def test_a_rejected_value_names_the_field_and_the_error_never_the_text(published):
    start()
    journey.record(event(
        "setup.validation_failed", step_key="custom_link",
        error_key="link-not-recognized", attempt_n=2,
    ))

    line = lines_now()[-1]
    assert "custom_link" in line
    assert "link-not-recognized" in line
    assert "try 2" in line


def test_an_edit_lists_the_fields_that_changed_and_no_values(published):
    start()
    journey.record(event("config.changed", changed_keys=["mode", "answer"]))

    line = published[-1]["lines"][-1]
    assert "`mode`" in line and "`answer`" in line
    assert published[-1]["result"] == "edited"


def test_an_edit_without_key_names_still_says_how_many_changed(published):
    start()
    journey.record(event("config.changed", changed_count=3))

    assert "3 field(s)" in published[-1]["lines"][-1]  # config.changed is terminal


@pytest.mark.parametrize("event_name,outcome", [
    ("setup.completed", "saved"),
    ("setup.discarded", "discarded"),
    ("setup.abandoned", "abandoned"),
    ("feature.disabled", "disabled"),
    ("feature.paused", "paused"),
])
def test_each_terminal_event_closes_the_story_with_its_own_outcome(
    published, event_name, outcome
):
    start()
    journey.record(event(event_name, step_key="mode", steps_viewed=2))

    assert published[-1]["result"] == outcome
    assert published[-1]["finished"] is True
    assert journey.get(SESSION) is None, "a closed session must not keep collecting"


def test_finalizing_twice_does_not_reopen_or_duplicate(published):
    start()
    journey.record(event("setup.completed", steps_viewed=2))
    renders_after_close = len(published)

    journey.finalize(SESSION, "abandoned")

    assert len(published) == renders_after_close, (
        "a timeout firing after a save must not publish again"
    )


def test_lines_arriving_after_the_close_are_dropped(published):
    start()
    journey.record(event("setup.completed", steps_viewed=1))
    journey.record(event("setup.step_viewed", step_key="late", step_action="options"))

    assert "step: late (options)" not in published[-1]["lines"]


def test_an_event_with_no_sentence_adds_no_line(published):
    start()
    journey.record(event("setup.step_completed", step_key="mode", ms_on_step=100))

    assert lines_now() == []


def test_the_message_is_not_rewritten_on_every_step(published):
    """The Discord edit bucket is per channel, and every session in the bot
    shares one. Rewriting on each step would saturate that channel long before
    any single session was noisy, so the story is published when it opens and
    when it ends — the middle is a click away."""
    start()
    assert len(published) == 1, "opening posts the message"

    for index in range(10):
        journey.record(event(
            "setup.step_viewed", step_key=f"s{index}", step_action="options"
        ))

    assert len(published) == 1, "ten steps must not cost ten edits"

    journey.record(event("setup.completed", steps_viewed=10))
    assert len(published) == 2, "the outcome is worth one edit"


def test_a_failure_is_published_immediately(published):
    """The one thing not worth waiting for."""
    start()
    journey.record(event("setup.step_viewed", step_key="mode", step_action="options"))
    assert len(published) == 1

    journey.record(event("command.failed", error_type="TimeoutError"))
    assert len(published) == 2


def test_a_story_can_be_rebuilt_from_storage_for_the_refresh_button(deps):
    """Refreshing costs one read: the steps were never only in memory."""
    from app.services import analytics_sink

    analytics.reset()
    journey.install()
    start()
    for step in ("mode", "permissions"):
        analytics.emit(
            "setup.step_viewed", guild_id="1", user_id="9", feature="block_links",
            source="slash", session_id=SESSION, step_key=step,
            step_action="options", step_index=1,
        )
    analytics.flush()
    journey.clear()

    rebuilt = journey.rebuild(SESSION)

    assert rebuilt is not None
    messages = [line["message"] for line in rebuilt.lines]
    assert "step: Blocking mode (options)" in messages
    assert "step: Permissions (options)" in messages


def test_refreshing_a_stored_session_renders_without_a_timezone_clash(deps):
    """Regression: Mongo hands timestamps back without a timezone, while the
    trace clock is UTC-aware. Subtracting them raised TypeError inside the
    button callback, so the refresh silently failed and Discord showed the user
    an interaction timeout."""
    from app.logger import build_trace_embed

    analytics.reset()
    journey.install()
    start()
    analytics.emit(
        "setup.step_viewed", guild_id="1", user_id="9", feature="block_links",
        source="slash", session_id=SESSION, step_key="mode",
        step_action="options", step_index=1,
    )
    analytics.flush()
    journey.clear()

    for document in deps.mongo_client.guild.analytics_events._data:
        document["ts"] = document["ts"].replace(tzinfo=None)

    story = journey.rebuild(SESSION)

    assert isinstance(story.duration_ms, int)
    assert build_trace_embed(story).description


def test_rebuilding_an_unfinished_session_reports_it_as_in_progress(deps):
    from app.services import analytics_sink

    analytics.reset()
    journey.install()
    start()
    analytics.emit(
        "setup.step_viewed", guild_id="1", user_id="9", feature="block_links",
        source="slash", session_id=SESSION, step_key="mode",
        step_action="options", step_index=1,
    )
    analytics.flush()

    assert journey.rebuild(SESSION).result == "in progress"


def test_rebuilding_a_session_whose_view_expired_says_abandoned(deps):
    from datetime import datetime, timedelta, timezone

    analytics.reset()
    journey.install()
    start()
    analytics.emit(
        "setup.step_viewed", guild_id="1", user_id="9", feature="block_links",
        source="slash", session_id=SESSION, step_key="mode",
        step_action="options", step_index=1,
    )
    analytics.flush()
    journey.clear()

    old = datetime.now(timezone.utc) - timedelta(hours=3)
    for document in deps.mongo_client.guild.analytics_events._data:
        document["ts"] = old

    assert journey.rebuild(SESSION).result == "abandoned", (
        "a message left stale by a restart must repair itself when refreshed"
    )


def test_the_interaction_trace_hands_its_lines_over_instead_of_posting_twice():
    with trace_scope("moderations block links", guild_id="1", user_id="9",
                     source="slash") as trace:
        trace.add("command invoked")
        story = start()
        story.is_journey = True
        trace.supersede(story)

    assert [line["message"] for line in story.lines] == ["command invoked"]
    assert trace.is_noteworthy is False, "the trace must not post its own message"


def test_a_publisher_that_explodes_never_breaks_the_session():
    journey.set_publisher(lambda story: 1 / 0)
    start()
    journey.record(event("setup.step_viewed", step_key="mode", step_action="options"))
    journey.finalize(SESSION, "saved")


def test_the_observer_reaches_the_journey_through_a_normal_emit(published):
    analytics.reset()
    journey.install()
    start()

    analytics.emit(
        "feature.setup_opened", guild_id="1", user_id="9",
        feature="block_links", source="slash", session_id=SESSION,
    )

    assert "setup opened" in lines_now()


def test_an_observer_that_explodes_never_breaks_the_emit():
    analytics.reset()
    analytics.register_observer(lambda envelope: 1 / 0)

    assert analytics.emit("command.invoked", command="ping", source="slash")
    assert analytics.stats()["observer_errors"] == 1


def test_the_timeline_cap_still_applies_to_a_long_session(published):
    start()
    for index in range(constants.ANALYTICS_MAX_PROPS + 40):
        journey.record(event(
            "setup.step_viewed", step_key=f"s{index}", step_action="options"
        ))

    from app.constants import LogTypes as logconstants
    assert len(lines_now()) <= logconstants.TRACE_MAX_LINES


# --------------------------------------------------------------------------
# Adding items is a step, not the end of the session
# --------------------------------------------------------------------------

def test_a_second_item_added_still_reaches_the_timeline(published):
    """Broke as: `feature.item_added` was made a terminal outcome so the log
    embed could be titled "Item Added". That closed the session on the first
    item and dropped the message from `_JOURNEYS`, so everything after it —
    further items, and the `saved` that ends the setup — reached nothing.

    A manager exists to add several items. `_line_for` renders these as steps,
    which is what they are.
    """
    start()
    journey.record(event("feature.item_added"))
    journey.record(event("feature.item_added"))
    journey.record(event("feature.item_removed"))

    assert lines_now().count("➕ item added") == 2
    assert "➖ item removed" in lines_now()


def test_adding_an_item_leaves_the_session_open(published):
    start()
    journey.record(event("feature.item_added"))

    story = journey.get(SESSION)
    assert story is not None, "the session was closed by a step"
    assert story.finished_at is None
    assert story.result == "in progress"


def test_a_setup_that_adds_items_still_records_the_save(published):
    start()
    journey.record(event("feature.item_added"))
    journey.record(event("setup.completed", steps_viewed=3))

    assert published[-1]["result"] == "saved"
    assert published[-1]["finished"] is True


def test_the_last_action_is_remembered_for_the_title(published):
    """What the embed title needs, without touching the session lifecycle."""
    start()
    journey.record(event("feature.item_added"))

    assert journey.get(SESSION).last_action == "added"


def test_a_terminal_outcome_outranks_the_last_action(published):
    start()
    journey.record(event("feature.item_added"))
    journey.record(event("setup.discarded"))

    from app import logger as logger_module

    story = published[-1]
    assert story["result"] == "discarded"

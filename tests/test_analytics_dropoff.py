"""Where a setup died, and why — reconstructed from the session's events.

The certainty here rests on one fact about the engine: the view a session lives
in dies after `LONG_TIMEOUT_SECONDS`. Once that has passed with no terminal
event, the attempt *cannot* finish, so "abandoned at step X" stops being a
guess. What the data never claims is the human reason — only what failed.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import ViewConstants as view_constants
from app.data import analytics as analytics_data
from app.services import admin_analytics, analytics, analytics_reports, analytics_sink

pytestmark = pytest.mark.unit

FEATURE = "block_links"


def flush():
    analytics.flush()


def open_setup(session, guild="1"):
    analytics.emit(
        "feature.setup_opened", guild_id=guild, user_id="9", feature=FEATURE,
        source="slash", session_id=session,
    )


def view_step(session, step, index, guild="1"):
    analytics.emit(
        "setup.step_viewed", guild_id=guild, user_id="9", feature=FEATURE,
        source="slash", session_id=session, step_key=step,
        step_action="options", step_index=index,
    )


def age_session(session, minutes):
    """Push a session's events into the past so the view would have expired."""
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    for collection in [analytics_data.mongo_client.guild.analytics_events]:
        for document in collection._data:
            if document.get("session_id") == session:
                document["ts"] = moment


def test_an_expired_session_reports_the_step_it_reached():
    open_setup("s1")
    view_step("s1", "mode", 1)
    view_step("s1", "permissions", 2)
    flush()
    age_session("s1", minutes=view_constants.LONG_TIMEOUT_SECONDS // 60 + 5)

    session = analytics_reports.setup_sessions(FEATURE)[0]
    assert session["outcome"] == "expired"
    assert session["last_step"] == "permissions"


def test_a_session_still_inside_the_timeout_is_open_not_abandoned():
    open_setup("s2")
    view_step("s2", "mode", 1)
    flush()

    session = analytics_reports.setup_sessions(FEATURE)[0]
    assert session["outcome"] == "open", (
        "an attempt the user may still be working on must not count as lost"
    )
    assert analytics_reports.dropoff(FEATURE) == []


def test_a_completed_session_is_never_counted_as_a_dropoff():
    open_setup("s3")
    view_step("s3", "mode", 1)
    analytics.emit(
        "setup.completed", guild_id="1", user_id="9", feature=FEATURE,
        source="slash", session_id="s3", duration_ms=1000, steps_viewed=1,
    )
    flush()
    age_session("s3", minutes=120)

    assert analytics_reports.setup_sessions(FEATURE)[0]["outcome"] == "completed"
    assert analytics_reports.dropoff(FEATURE) == []


def test_a_discard_reports_the_step_it_was_discarded_from():
    open_setup("s4")
    view_step("s4", "mode", 1)
    view_step("s4", "custom_link", 2)
    analytics.emit(
        "setup.discarded", guild_id="1", user_id="9", feature=FEATURE,
        session_id="s4", step_key="custom_link", steps_viewed=2,
        validation_failures=0, back_count=0,
    )
    flush()

    session = analytics_reports.setup_sessions(FEATURE)[0]
    assert session["outcome"] == "discarded"
    assert session["last_step"] == "custom_link"
    assert session["reason"] == "discarded-without-error"


def test_a_session_that_kept_failing_a_field_says_so():
    open_setup("s5")
    view_step("s5", "custom_link", 1)
    for _ in range(3):
        analytics.emit(
            "setup.validation_failed", guild_id="1", user_id="9", feature=FEATURE,
            source="slash", session_id="s5", step_key="custom_link",
            validation="validate_link", error_key="link-not-recognized",
        )
    flush()
    age_session("s5", minutes=120)

    session = analytics_reports.setup_sessions(FEATURE)[0]
    assert session["reason"] == "repeated-validation-failure"
    assert session["validation_failures"] == 3
    assert session["failed_keys"] == ["link-not-recognized"] * 3


def test_a_session_that_left_with_nothing_failing_says_exactly_that():
    open_setup("s6")
    view_step("s6", "permissions", 1)
    flush()
    age_session("s6", minutes=120)

    session = analytics_reports.setup_sessions(FEATURE)[0]
    assert session["reason"] == "left-without-a-recorded-problem", (
        "the absence of a recorded problem must not be dressed up as a cause"
    )


def test_the_dropoff_report_ranks_the_steps_that_lose_the_most_people():
    for index, session in enumerate(["a1", "a2", "a3"]):
        open_setup(session)
        view_step(session, "custom_link", 2)
    open_setup("b1")
    view_step("b1", "permissions", 3)
    flush()
    for session in ["a1", "a2", "a3", "b1"]:
        age_session(session, minutes=120)

    rows = analytics_reports.dropoff(FEATURE)
    assert rows[0]["step_key"] == "custom_link"
    assert rows[0]["lost"] == 3
    assert rows[1]["step_key"] == "permissions"


def test_the_screen_separates_discarded_from_expired():
    open_setup("c1")
    view_step("c1", "mode", 1)
    analytics.emit(
        "setup.discarded", guild_id="1", user_id="9", feature=FEATURE,
        session_id="c1", step_key="mode",
    )
    open_setup("c2")
    view_step("c2", "mode", 1)
    flush()
    age_session("c1", minutes=120)
    age_session("c2", minutes=120)

    embed = admin_analytics.build_dropoff_embed(FEATURE)
    assert "1 discarded, 1 expired" in embed.description
    assert "the view timed out" in embed.footer.text


def test_a_single_session_can_be_read_on_its_own():
    open_setup("d1")
    view_step("d1", "custom_link", 1)
    analytics.emit(
        "setup.validation_failed", guild_id="1", user_id="9", feature=FEATURE,
        source="slash", session_id="d1", step_key="custom_link",
        validation="validate_link", error_key="link-not-recognized",
    )
    flush()
    age_session("d1", minutes=120)

    session = analytics_reports.setup_sessions(FEATURE)[0]
    embed = admin_analytics.build_session_embed(session)
    assert "custom_link" in embed.description
    assert "link-not-recognized" in embed.description

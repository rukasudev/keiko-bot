"""Time on each step, and what people picked when the YAML declares the list.

The number is the interval between two interactions. That is a real signal for
finding steps worth looking at, and it is not evidence of confusion — the
report says so and the tests keep that honest.
"""
import pytest

from app.services import analytics, analytics_reports, analytics_sink
from app.views.form_state import FormSession

pytestmark = pytest.mark.unit

FEATURE = "block_links"


def flush():
    analytics.flush()


def step_completed(step_key, ms, choice=None, feature=FEATURE):
    analytics.emit(
        "setup.step_completed", guild_id="1", user_id="9", feature=feature,
        source="slash", session_id="s", step_key=step_key,
        step_action="options", ms_on_step=ms, choice=choice,
    )


def test_the_first_step_has_no_previous_interval_to_report():
    session = FormSession()
    assert session.record_step({"key": "mode"}) is None


def test_the_interval_belongs_to_the_step_that_just_left():
    session = FormSession()
    session.record_step({"key": "mode"})
    elapsed = session.record_step({"key": "permissions"})

    assert isinstance(elapsed, int)
    assert session.last_step_key == "permissions", (
        "the session moves on, but the interval described the step before it"
    )


def test_closing_measures_the_final_step_nothing_else_would():
    session = FormSession()
    session.record_step({"key": "confirm"})

    assert isinstance(session.close_step(), int)


def test_closing_before_any_step_is_not_an_error():
    assert FormSession().close_step() is None


def test_steps_are_ranked_by_median_not_by_the_worst_case():
    for ms in (1000, 1000, 1000, 90000):
        step_completed("permissions", ms)
    for ms in (40000, 45000, 50000):
        step_completed("custom_link", ms)
    flush()

    rows = analytics_reports.step_durations(FEATURE)

    assert rows[0]["step_key"] == "custom_link", (
        "one slow outlier must not outrank a step that is consistently slow"
    )
    assert rows[0]["median_ms"] == 45000
    assert rows[1]["step_key"] == "permissions"
    assert rows[1]["slowest_ms"] == 90000


def test_a_step_without_a_measured_interval_is_skipped():
    step_completed("mode", None)
    flush()

    assert analytics_reports.step_durations(FEATURE) == []


def test_choices_are_tabulated_only_when_the_form_declared_them():
    step_completed("mode", 1000, choice="block_all")
    step_completed("mode", 1200, choice="block_all")
    step_completed("mode", 900, choice="allow_all")
    step_completed("answer", 5000, choice=None)
    flush()

    distribution = analytics_reports.choice_distribution(FEATURE)

    assert distribution[f"{FEATURE}/mode"] == {"block_all": 2, "allow_all": 1}
    assert f"{FEATURE}/answer" not in distribution


def test_the_report_is_empty_and_calm_when_nothing_was_measured():
    assert analytics_reports.step_durations() == []
    assert analytics_reports.choice_distribution() == {}

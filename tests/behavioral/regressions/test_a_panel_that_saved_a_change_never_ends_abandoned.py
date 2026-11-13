"""Regression: a manager panel that saved a change ends completed, never abandoned.

What broke: once a change saved from the panel drew the panel again instead of
closing the message (ux-341), the session stayed open, and when nobody clicked
again it expired like a panel nobody used: `setup.abandoned` fired, its story
closed as `⌛ abandoned` and `keiko_form_sessions_total` counted it `expired`.
Before, the commit closed the session as `completed` and nothing was abandoned.

Shared behavior affected: `on_expired` (app/settings/form/form.py), the
manager's saved-change bookkeeping (`FormSession.changes_saved`, set by
`_on_commit_succeeded` in app/settings/form/manager.py), and the adapter's
journey closing (`Runtime.expire_stale`, `Runtime._close`,
`observability.ended_as`), shared by every manager panel.

Exposed by: reminders_birthday, a member added from the panel and the panel
left open.

Must remain guaranteed: a panel that saved at least one change and then
expires emits no `setup.abandoned`, counts `completed`, and closes its story
as its last change; a panel that saved nothing still ends abandoned and
counts `expired`.
"""
import pytest
from prometheus_client import REGISTRY

from app.core import journey
from tests.behavioral.golden.paths.reminders_birthday import (
    _complete_member_card,
    _guild,
    _seed_config,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]

FEATURE = "reminders_birthday"


@pytest.fixture
def story():
    """Every session story the journey published, read live at assert time."""
    seen = {}
    journey.clear()
    journey.set_publisher(lambda item: seen.__setitem__(item.session_id, item))
    journey.install()
    yield seen
    journey.clear()
    journey.set_publisher(None)


def _sessions(status):
    counted = REGISTRY.get_sample_value(
        "keiko_form_sessions_total", {"feature": FEATURE, "status": status}
    )
    return counted or 0.0


async def _panel(scenario_factory, deps):
    _seed_config(deps)
    return await scenario_factory(locale="pt-br", guild=_guild()).start_command(
        FEATURE
    )


def _names(recorded):
    return [event["event"] for event in recorded]


async def test_a_panel_that_added_a_member_and_expired_ends_completed(
    scenario_factory, deps, analytics_events, story
):
    completed, expired = _sessions("completed"), _sessions("expired")
    scenario = await _panel(scenario_factory, deps)
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await _complete_member_card(scenario, "pt-br")

    await scenario.expire()

    assert "setup.abandoned" not in _names(analytics_events)
    assert "feature.item_added" in _names(analytics_events)
    assert _sessions("expired") == expired
    assert _sessions("completed") == completed + 2, "the panel and its member card"
    (told,) = story.values()
    assert told.finished_at and told.result == "added"


async def test_a_panel_that_saved_nothing_and_expired_ends_abandoned(
    scenario_factory, deps, analytics_events, story
):
    completed, expired = _sessions("completed"), _sessions("expired")
    scenario = await _panel(scenario_factory, deps)

    await scenario.expire()

    assert _names(analytics_events).count("setup.abandoned") == 1
    assert _sessions("expired") == expired + 1
    assert _sessions("completed") == completed
    (told,) = story.values()
    assert told.finished_at and told.result == "abandoned"

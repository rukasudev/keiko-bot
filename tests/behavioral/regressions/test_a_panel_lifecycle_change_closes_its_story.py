"""Regression: a pause, resume or disable from the panel closes the panel's story.

What broke: the adapter closed the journey of a completed session only when it
had saved a change before or when a setup turned the feature on. A pause,
resume or disable from a panel that saved nothing else ended `completed` with
no close of its own: its story was closed only by the `feature.paused` /
`feature.unpaused` / `feature.disabled` events reaching the journey observer,
so with the events off (`ANALYTICS_ENABLED=false`) every such story stayed
`⏳ in progress` and its journey was kept in memory for the life of the process.

Shared behavior affected: `Runtime._close` (app/forms/discord/callbacks.py)
and `observability.ended_as` / `observability.completed_as`, the one place
every root session's story ends, shared by every manager panel.

Exposed by: the review of `_close` (beta.2 corrections, K2), on block_links
and notifications_twitch panels.

Since PR8 a pause and a resume keep the panel open (the panel is drawn again
with the feature's new state), so they are steps of the panel's story, which
ends with the panel; a delete still closes it at once.

Must remain guaranteed: a delete closes the panel's story as `disabled`; a
pause or a resume leaves it open and, when the panel ends, the story closes
named by that last action (`paused`, `resumed`); both hold whether or not the
product events reach the journey, and no journey is left open behind it.
"""

import importlib

import pytest

from app.core import analytics, journey
from tests.behavioral.golden.paths.common import seed_document

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]

OUTCOMES = {"pause": "paused", "unpause": "resumed", "disable": "disabled"}


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


@pytest.mark.parametrize("events_on", [True, False], ids=["events-on", "events-off"])
@pytest.mark.parametrize("action", ["pause", "unpause"])
@pytest.mark.parametrize("form", ["block_links", "notifications_twitch"])
async def test_a_pause_or_resume_is_a_step_its_panel_closes_with(
    scenario_factory, deps, story, monkeypatch, form, action, events_on
):
    paths = importlib.import_module(f"tests.behavioral.golden.paths.{form}")
    seed_document(deps, form, paths.PAUSED if action == "unpause" else paths.ENABLED)
    monkeypatch.setattr(analytics, "_ENABLED", events_on)
    scenario = await scenario_factory(locale="pt-br").start_command(form)

    await scenario.click(action)

    (told,) = story.values()
    assert not told.finished_at, f"a panel {action} closed the panel's story"
    assert journey.get(told.session_id) is told

    await scenario.expire()

    assert told.finished_at, f"the story of a panel {action} was left open"
    assert told.result == OUTCOMES[action]
    assert journey.get(told.session_id) is None, "the journey leaked in memory"


@pytest.mark.parametrize("events_on", [True, False], ids=["events-on", "events-off"])
@pytest.mark.parametrize("form", ["block_links", "notifications_twitch"])
async def test_a_delete_from_the_panel_ends_its_story_at_once(
    scenario_factory, deps, story, monkeypatch, form, events_on
):
    paths = importlib.import_module(f"tests.behavioral.golden.paths.{form}")
    seed_document(deps, form, paths.ENABLED)
    monkeypatch.setattr(analytics, "_ENABLED", events_on)
    scenario = await scenario_factory(locale="pt-br").start_command(form)

    await scenario.click("disable")
    await scenario.click("delete_all")

    (told,) = story.values()
    assert told.finished_at, "the story of a panel delete was left open"
    assert told.result == "disabled"
    assert journey.get(told.session_id) is None, "the journey leaked in memory"

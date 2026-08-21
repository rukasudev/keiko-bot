"""Behavioral scenarios: what a real configuration flow reports.

These drive the real engine and assert the product events that come out of it.
The point of the architecture is that no command was instrumented — every event
below is produced by shared code, so a new YAML command inherits all of it.
"""
import pytest

from app.services import analytics

pytestmark = pytest.mark.behavioral

GUILD_ID = "123456789"
LATER = "Depois"


def events_named(recorded, name):
    return [event for event in recorded if event["event"] == name]


def names(recorded):
    return [event["event"] for event in recorded]


async def test_a_completed_setup_reports_the_whole_funnel(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    assert events_named(analytics_events, "feature.setup_opened")
    assert events_named(analytics_events, "setup.step_viewed")

    completed = events_named(analytics_events, "setup.completed")
    assert len(completed) == 1
    assert completed[0]["feature"] == "block_links"
    assert completed[0]["guild_id"] == GUILD_ID
    assert completed[0]["props"]["steps_viewed"] > 0
    assert completed[0]["props"]["duration_bucket"] == "<1m"

    assert events_named(analytics_events, "feature.enabled")
    await scenario.finish()


async def test_every_event_of_one_attempt_shares_a_session_id(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    sessions = {
        event["session_id"] for event in analytics_events
        if event["event"].startswith(("setup.", "feature.setup"))
    }
    assert len(sessions) == 1
    await scenario.finish()


async def test_the_origin_is_carried_from_the_entry_point_to_the_end(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    completed = events_named(analytics_events, "setup.completed")[0]
    assert completed["source"] == "slash"
    await scenario.finish()


async def test_steps_are_reported_with_their_key_and_action_not_their_answer(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()

    steps = events_named(analytics_events, "setup.step_viewed")
    assert steps
    for step in steps:
        assert step["props"]["step_key"]
        assert step["props"]["step_action"]
        assert "value" not in step["props"]
        assert "answer" not in step["props"]
    await scenario.finish()


async def test_no_typed_text_ever_reaches_an_event(scenario_factory, analytics_events):
    """The privacy rule as an executable assertion, not documentation."""
    secret = "meu servidor secreto convite discord.gg/xyz"

    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")
    await scenario.submit_modal({"resposta": secret})
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    for event in analytics_events:
        for value in (event.get("props") or {}).values():
            assert secret not in str(value), event["event"]
    await scenario.finish()


async def test_each_step_reports_how_long_it_was_on_screen(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    completed = events_named(analytics_events, "setup.step_completed")
    assert completed, "no step reported its time"

    for event in completed:
        assert event["props"]["step_key"]
        assert event["props"]["step_action"]

    measured = [e for e in completed if "ms_on_step" in e["props"]]
    assert measured, "at least the steps after the first must be measured"
    assert all(e["props"]["ms_on_step"] >= 0 for e in measured)

    steps_seen = {e["props"]["step_key"] for e in completed}
    assert "link_settings" in steps_seen
    await scenario.finish()


async def test_a_declared_option_is_recorded_but_a_typed_answer_is_not(
    scenario_factory, analytics_events
):
    """The card's `mode` comes from a list written in the YAML; the `answer`
    is typed. Only the first may ever be tabulated."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")
    await scenario.submit_modal({"resposta": "texto que ninguem deve ver"})
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    for event in events_named(analytics_events, "setup.step_completed"):
        choice = event["props"].get("choice")
        assert "texto que ninguem deve ver" not in str(choice)

    await scenario.finish()


async def test_going_back_is_reported_and_counted(scenario_factory, analytics_events):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.click("back")

    assert events_named(analytics_events, "setup.step_back")
    await scenario.finish()


async def test_a_discarded_setup_reports_no_completion(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("cancel")
    await scenario.click("Descartar tudo")

    assert events_named(analytics_events, "setup.discarded")
    assert not events_named(analytics_events, "setup.completed")
    assert not events_named(analytics_events, "feature.enabled")
    await scenario.finish()


async def test_keeping_a_setup_after_cancel_reports_the_recovery(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("cancel")
    await scenario.click("Continuar configurando")

    assert events_named(analytics_events, "setup.discard_recovered")
    assert not events_named(analytics_events, "setup.discarded")
    await scenario.finish()


async def test_the_manager_reports_an_opening_not_a_setup(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()
    await scenario.finish()

    analytics_events.clear()

    cog_data = {"guild_id": GUILD_ID, "enabled": True, "mode": "block_all"}
    manager = await scenario_factory(locale="pt-br").start_manager("block_links", cog_data)
    assert events_named(analytics_events, "feature.manager_opened")
    assert not events_named(analytics_events, "feature.setup_opened")
    await manager.finish()


async def test_analytics_failure_never_breaks_the_flow(
    scenario_factory, monkeypatch
):
    """The requirement that made the queue lossy: a broken sink costs an
    event, never a configuration."""
    def explode(*args, **kwargs):
        raise RuntimeError("analytics is down")

    monkeypatch.setattr(analytics, "_emit", explode)

    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"enabled": True}
    )
    await scenario.finish()

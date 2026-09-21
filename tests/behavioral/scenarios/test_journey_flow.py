"""Behavioral: the session message a real flow produces, end to end.

One message per attempt, whatever the attempt turns into. These drive the real
engine, so what they assert is what lands in the admin channel.
"""
import pytest

from app.services import analytics, journey

pytestmark = pytest.mark.behavioral

GUILD_ID = "123456789"
LATER = "Depois"


@pytest.fixture(autouse=True)
def journey_isolation():
    journey.clear()
    journey.set_publisher(None)
    yield
    journey.clear()
    journey.set_publisher(None)


@pytest.fixture
def story():
    """Every session that reached the publisher, read live at assert time."""
    seen = {}
    journey.set_publisher(lambda item: seen.__setitem__(item.session_id, item))
    journey.install()
    return seen


def only(seen):
    assert len(seen) == 1, f"expected one session, got {list(seen)}"
    item = list(seen.values())[0]
    return {
        "name": item.name,
        "result": item.result,
        "lines": [line["message"] for line in item.lines],
        "finished": bool(item.finished_at),
        "footnote": item.footnote,
    }


async def test_a_completed_setup_ends_as_saved(scenario_factory, story):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    session = only(story)
    assert session["result"] == "saved"
    assert session["finished"] is True
    assert any("setup opened" in line for line in session["lines"])
    assert any("step:" in line for line in session["lines"])
    await scenario.finish()


async def test_one_session_produces_one_message_not_one_per_click(
    scenario_factory, story
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    assert len(story) == 1, "every click must edit the same message"
    await scenario.finish()


async def test_discarding_with_the_button_ends_as_discarded(scenario_factory, story):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("cancel")
    await scenario.click("Descartar tudo")

    session = only(story)
    assert session["result"] == "discarded"
    assert any("discarded at" in line for line in session["lines"])
    await scenario.finish()


async def test_a_timeout_ends_as_abandoned_at_the_last_step(scenario_factory, story):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")

    await scenario.expire()

    session = only(story)
    assert session["result"] == "abandoned"
    assert any("expired at" in line for line in session["lines"])
    await scenario.finish()


async def test_a_timeout_after_a_save_does_not_reopen_the_story(
    scenario_factory, story
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    await scenario.expire()

    assert only(story)["result"] == "saved"
    await scenario.finish()


async def test_a_validation_failure_shows_the_field_not_what_was_typed(
    scenario_factory, story
):
    secret = "meu convite secreto discord.gg/xyz"

    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")
    await scenario.submit_modal({"resposta": secret})
    await scenario.click("done")
    await scenario.click(LATER)
    await scenario.confirm()
    await scenario.confirm()

    session = only(story)
    for line in session["lines"]:
        assert secret not in line
    assert session["footnote"] is None or secret not in session["footnote"]
    await scenario.finish()


async def test_editing_through_the_manager_lists_the_changed_fields(
    scenario_factory, deps, story
):
    from app.services.block_links import normalize_block_links_config

    legacy = {"guild_id": GUILD_ID, "enabled": True, "mode": "block_all",
              "answer": "Resposta antiga"}
    deps.mongo_client.guild["block_links"].insert_one(dict(legacy))

    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(legacy)
    )
    await scenario.click("section:link_settings")
    await scenario.click("customize:2")
    await scenario.submit_modal({"resposta": "Resposta nova"})
    await scenario.click("done")

    session = only(story)
    assert session["result"] == "edited"
    edited = [line for line in session["lines"] if line.startswith("🔧")]
    assert edited, "the edit must appear on the timeline"
    assert "Resposta nova" not in edited[0], "field names only, never values"
    await scenario.finish()


async def test_the_manager_session_starts_from_the_manager_not_a_setup(
    scenario_factory, story
):
    cog_data = {"guild_id": GUILD_ID, "enabled": True, "mode": "block_all"}
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", cog_data
    )

    session = only(story)
    assert "manager opened" in session["lines"]
    assert "setup opened" not in session["lines"]
    await scenario.finish()


async def test_disabling_analytics_also_silences_the_journey(
    scenario_factory, story, monkeypatch
):
    """The journey renders product events, so turning them off turns it off —
    the log falls back to the per-interaction trace."""
    monkeypatch.setattr(analytics, "_ENABLED", False)

    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()

    assert all(not item.lines for item in story.values())
    await scenario.finish()

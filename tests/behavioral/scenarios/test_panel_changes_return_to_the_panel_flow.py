"""Behavioral scenarios: a change saved from the manager panel returns to the panel.

Adicionar, Editar (of an item or of a card) and Remover end on the panel drawn
again over what is saved now, on the message the admin was using, instead of a
closed "Item adicionado" / "editado" / "removido" embed. What the feature
records about the change (its product events, the session's story) is the
same as when the message closed.
"""
import pytest

from app.services.utils import ml
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths.common import open_manager
from tests.behavioral.golden.paths.reminders_birthday import (
    _complete_member_card,
    _guild,
    _seed_config,
)

pytestmark = pytest.mark.behavioral

LOCALE = "pt-br"
CLOSED_TITLES = [
    ml(f"commands.command-events.{kind}.title", locale=LOCALE)
    for kind in ("added", "edited", "removed")
]


def _last_screen(scenario):
    return next(
        event
        for event in reversed(scenario.outputs)
        if event.get("actor") == "bot" and event.get("components_v2")
    )


def _closed_embeds(scenario):
    return [
        event
        for event in scenario.outputs
        if any(
            title.split("**")[0] in ((event.get("embed") or {}).get("title") or "")
            for title in CLOSED_TITLES
        )
    ]


def _names(recorded):
    return [event["event"] for event in recorded]


async def test_a_birthday_added_from_the_panel_returns_to_the_updated_panel(
    scenario_factory, deps, analytics_events
):
    _seed_config(deps)
    scenario = await scenario_factory(locale=LOCALE, guild=_guild()).start_command(
        "reminders_birthday"
    )
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    card = _last_screen(scenario)["message"]

    await _complete_member_card(scenario, LOCALE)

    panel = _last_screen(scenario)
    assert panel["message"] == card, "the panel is drawn on the card's own message"
    assert "<@777>" in str(panel), "the panel lists the member just added"
    assert _closed_embeds(scenario) == []
    scenario.expect_component(label_or_action="add")
    assert "feature.item_added" in _names(analytics_events)


@pytest.mark.usefixtures("outside_dev")
async def test_a_streamer_edited_from_the_panel_returns_to_the_updated_panel(
    scenario_factory, deps, analytics_events
):
    scenario = await open_manager(scenario_factory, deps, LOCALE, twitch.FORM, twitch._seed)
    await scenario.click("section:notifications$0")
    await twitch._channel(scenario, "announcements")
    await scenario.click("done")

    panel = str(_last_screen(scenario))
    assert "<#102>" in panel and "gaules" in panel, "the panel shows the edited item"
    assert _closed_embeds(scenario) == []
    scenario.expect_component(label_or_action="pause")
    assert "config.changed" in _names(analytics_events)


@pytest.mark.usefixtures("outside_dev")
async def test_a_streamer_removed_from_the_panel_returns_to_the_updated_panel(
    scenario_factory, deps, analytics_events
):
    scenario = await open_manager(scenario_factory, deps, LOCALE, twitch.FORM, twitch._seed)
    await scenario.click("remove_this:notifications$0")
    await scenario.click("remove_items")

    panel = str(_last_screen(scenario))
    assert "cellbit" in panel and "gaules" not in panel, "the panel lists what is left"
    assert _closed_embeds(scenario) == []
    scenario.expect_component(label_or_action="pause")
    assert "feature.item_removed" in _names(analytics_events)


@pytest.fixture
def outside_dev(deps):
    """The subscription hooks run only outside dev."""
    deps.bot.config.is_dev = lambda: False

"""Behavioral scenario: Cancel asks only when there is something to lose.

A setup with nothing answered yet closes at once on Cancelar, as a discard:
no "you will lose your settings" message, the controls taken off the screen.
Once something was answered, Cancelar asks to keep or discard, as before
(PR15 round 5, k03b).
"""
import discord
import pytest

from app.services import analytics
from app.services.utils import ml
from app.settings.discord.callbacks import RUNTIME
from app.settings.form.form_state import Status
from tests.behavioral.harness import locators

pytestmark = pytest.mark.behavioral


@pytest.fixture
def analytics_events(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        analytics, "emit", lambda name, **props: recorded.append(name)
    )
    return recorded


def _confirmations(scenario, locale):
    title = ml("errors.discard-settings-confirmation.title", locale)
    return [
        event
        for event in scenario.outputs
        if (event.get("embed") or {}).get("title", "").endswith(title)
    ]


def _buttons(scenario):
    return [
        item
        for item in locators.walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.Button)
    ]


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
@pytest.mark.parametrize("on_card", [False, True], ids=["intro", "card"])
async def test_cancel_with_nothing_answered_closes_at_once(
    scenario_factory, analytics_events, locale, on_card
):
    scenario = await scenario_factory(locale=locale).start("block_links")
    if on_card:
        await scenario.confirm()
    session_id = scenario.session.id

    await scenario.cancel()

    assert not _confirmations(scenario, locale), "nothing to lose, nothing asked"
    assert RUNTIME.store.get(session_id).status is Status.CANCELLED
    assert not [button for button in _buttons(scenario) if not button.disabled]
    assert "setup.discarded" in analytics_events
    await scenario.finish()


async def test_cancel_after_an_answer_asks_to_keep_or_discard(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.select_option("allow_all", target="mode")
    session_id = scenario.session.id

    await scenario.cancel()

    assert _confirmations(scenario, "pt-br")
    assert RUNTIME.store.get(session_id).status is Status.AWAITING
    await scenario.click(ml("buttons.cancel.discard", "pt-br"))
    assert RUNTIME.store.get(session_id).status is Status.CANCELLED
    assert "setup.discarded" in analytics_events
    await scenario.finish()


@pytest.mark.parametrize(
    "locale,title", [("pt-br", "Descartar Configurações?"), ("en-us", "Discard Settings?")]
)
async def test_the_confirmation_asks_to_discard_and_keeps_editing_in_grey(
    scenario_factory, locale, title
):
    """The confirmation is named after what it does, and continuing is not the green,
    primary-looking choice (PR15 round 6, m08)."""
    scenario = await scenario_factory(locale=locale).start("block_links")
    await scenario.confirm()
    await scenario.select_option("allow_all", target="mode")

    await scenario.cancel()

    assert _confirmations(scenario, locale)
    assert ml("errors.discard-settings-confirmation.title", locale) == title
    keep = next(b for b in _buttons(scenario) if b.label == ml("buttons.cancel.keep", locale))
    assert keep.style == discord.ButtonStyle.secondary
    await scenario.click(ml("buttons.cancel.discard", locale))
    await scenario.finish()


def test_the_help_runs_a_command_from_a_blue_button_without_an_emoji():
    """A blue ▶️ inside a green button looked broken; the run button is blue with its label
    only (PR15 rounds 6 and 7, m03)."""
    from app.components.buttons import ExecuteCommandButton

    button = ExecuteCommandButton("block_links", "pt-br")

    assert (button.label, button.emoji, button.style) == (
        "Executar",
        None,
        discord.ButtonStyle.primary,
    )

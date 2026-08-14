"""Cancel-is-last contract for the interactive views.

Keiko UI convention: the red Cancel button is ALWAYS the last button the
user sees in a view; Back (and any other late-added navigation) comes
before it. Pinned after the block_links card flow shipped option steps
rendering [options..., Cancelar, Voltar], with Voltar appended after the
Cancel button by Form._add_back_button. Covers every assembly path:
configuration card action row, options step with a late back button,
options step inside a composition, and the info (action: button) step.
"""
import discord
import pytest

from app.components.buttons import CancelButton

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]


def _flat_buttons(view):
    found = []

    def _walk(children):
        for child in children:
            if isinstance(child, discord.ui.Button):
                found.append(child)
            _walk(getattr(child, "children", None) or [])

    _walk(view.children)
    return found


def _assert_cancel_is_last(view):
    buttons = _flat_buttons(view)
    cancel_positions = [
        index for index, button in enumerate(buttons)
        if isinstance(button, CancelButton) or button.custom_id == "card_cancel"
    ]
    assert cancel_positions, "view under test must contain a cancel button"
    order = ", ".join(str(button.label) for button in buttons)
    assert cancel_positions[-1] == len(buttons) - 1, (
        f"cancel must be the last button in the view, got: {order}"
    )


async def test_configuration_card_action_row_has_cancel_last(scenario_factory):
    """Card action row: Done, Back, Cancel (back exists: intro precedes it)."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()

    scenario.expect_message(components_v2=True)
    _assert_cancel_is_last(scenario.current_message.view)
    await scenario.finish()


async def test_options_step_with_back_button_has_cancel_last(scenario_factory):
    """OptionsView builds with Cancel last, then the form appends Back:
    the view must still end with Cancel."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")

    scenario.expect_step("add_custom")
    _assert_cancel_is_last(scenario.current_message.view)
    await scenario.finish()


async def test_composition_options_step_has_cancel_last(scenario_factory):
    """Same guarantee inside a composition sub-form (match_type step)."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click("Sim")
    await scenario.submit_modal({"Digite o link ou site": "meusite.com.br/promo"})

    scenario.expect_step("match_type")
    _assert_cancel_is_last(scenario.current_message.view)
    await scenario.finish()


async def test_info_button_step_has_cancel_last(scenario_factory, deps):
    """show_buttons path (action: button): Confirm, Back, Cancel."""
    deps.bot.config.is_dev = lambda: False
    deps.twitch.add_user("gaules", user_id="111")

    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await scenario.select_option("general")
    await scenario.confirm()
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "gaules"})

    _assert_cancel_is_last(scenario.current_message.view)
    await scenario.finish()

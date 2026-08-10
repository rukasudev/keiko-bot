"""Contract: chrome of the Components V2 card screens.

Cards and their pickers are LayoutViews, so they never pass through
parse_form_dict_to_embed, where every other step renders its footer and where
option colors are resolved. Both were silently dropped: the configuration card
showed no footer at all, and a picker painted every option grey no matter what
the YAML declared. These pin both for every card-driven command.
"""
import discord
import pytest

from app.services.utils import ml
from tests.behavioral.harness.locators import walk_items

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

FOOTER = {
    "pt-br": "• Use o comando `/reportar` para me contar um bug",
    "en-us": "• Use the `/report` command to tell me a bug",
}


def _texts(view) -> str:
    return "\n".join(
        item.content or ""
        for item in walk_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    )


def _buttons(view):
    return [item for item in walk_items(view) if isinstance(item, discord.ui.Button)]


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_configuration_card_renders_the_step_footer(scenario_factory, locale):
    scenario = await scenario_factory(locale=locale).start("block_links")
    await scenario.confirm()

    rendered = _texts(scenario.current_message.view)
    assert f"-# {FOOTER[locale]}" in rendered, (
        "the card must render the step footer as subtext, like every other step"
    )
    await scenario.finish()


async def test_card_picker_renders_the_step_footer(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:0")

    rendered = _texts(scenario.current_message.view)
    assert f"-# {FOOTER['pt-br']}" in rendered, (
        "a picker screen is a full screen too, and keeps the footer"
    )
    await scenario.finish()


async def test_birthday_card_renders_the_step_footer(scenario_factory):
    """Second consumer: the footer comes from the shared card builder, not
    from block_links."""
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()

    assert "-# " in _texts(scenario.current_message.view)
    await scenario.finish()


async def test_card_option_buttons_use_the_style_declared_in_yaml(scenario_factory):
    """`options[].style` is honored by the card picker exactly as it is by a
    regular options step, through the shared resolve_option_style."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:0")

    options = [
        button for button in _buttons(scenario.current_message.view)
        if (button.custom_id or "").startswith("picker_option_")
    ]
    styles = [button.style for button in options]
    assert styles == [discord.ButtonStyle.success, discord.ButtonStyle.danger], (
        f"mode picker must paint allow-all green and block-all red, got {styles}"
    )
    labels = [button.label for button in options]
    assert labels[0].startswith("✅") and labels[1].startswith("🚫"), (
        f"each mode button carries its own emoji, got {labels}"
    )
    assert ml("buttons.back.label", locale="pt-br") in [
        button.label for button in _buttons(scenario.current_message.view)
    ]
    await scenario.finish()

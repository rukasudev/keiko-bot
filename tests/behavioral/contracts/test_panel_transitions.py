"""Shared contract: leaving a Components V2 panel.

Discord fixes a message's flags at send time — a message sent with a container
can never be edited back into an embed message, and vice versa. So every screen
reached from the manager panel has to replace it instead of editing it. This is
invisible in production until a button simply stops working, and invisible
offline unless a scenario actually drives it, so it is driven here.
"""
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

BLOCK_LINKS_COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "mode": "block_all",
    "answer": "Nada de links por aqui!",
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "allowed_chats": {"style": "channel", "values": "100"},
    "custom_links": {
        "style": "composition",
        "values": [{"link": {"value": "twitch.tv/jway"},
                    "match_type": {"value": "domain"}}],
    },
}


async def _panel(scenario_factory, deps, cog=None):
    from app.services.block_links import normalize_block_links_config

    cog = cog or dict(BLOCK_LINKS_COG)
    deps.mongo_client.guild["block_links"].insert_one(dict(cog))
    return await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(cog)
    )


async def test_panel_renders_as_a_container_without_an_embed(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps)

    scenario.expect_message(components_v2=True)
    assert not scenario.outputs[-1].get("embed"), \
        "a Components V2 message carries no embed"
    scenario.expect_configuration_values("youtube.com", "<#100>", "twitch.tv/jway")


async def test_each_section_carries_the_button_that_edits_it(scenario_factory, deps):
    """The point of the container: the action sits next to the thing it acts
    on, instead of behind a single Edit button and a dropdown."""
    from app.components.buttons import EditButton
    from app.views.manager_panel import SectionEditButton
    from tests.behavioral.harness.locators import walk_items

    scenario = await _panel(scenario_factory, deps)
    items = list(walk_items(scenario.current_message.view))

    edit_buttons = [item for item in items if isinstance(item, SectionEditButton)]
    assert {button.step_key for button in edit_buttons} == {
        "link_settings", "custom_links", "permissions"
    }, "one edit button per YAML section that owns settings"
    assert not [item for item in items if isinstance(item, EditButton)], \
        "the single global Edit button is gone: editing lives in the sections"


async def test_section_edit_button_opens_that_step_directly(scenario_factory, deps):
    """No dropdown in between: the panel replaces itself with the card of the
    section the user clicked."""
    scenario = await _panel(scenario_factory, deps)

    await scenario.click("section:link_settings")

    scenario.expect_message(components_v2=True)      # the card is CV2 as well
    card = scenario.current_message.view
    assert card.state["answer"] == "Nada de links por aqui!", \
        "the card opens hydrated with the saved configuration"


@pytest.mark.parametrize("button_key, event_key", [("pause", "paused"),
                                                   ("disable", "disabled")])
async def test_a_lifecycle_action_takes_the_panel_off_the_screen(
    scenario_factory, deps, button_key, event_key
):
    """`close_panel` is the only thing standing between a container panel and
    an InteractionResponded/flag error on every lifecycle button.

    Disable used to announce first and then edit the panel to drop its buttons.
    That works on an embed and never on a container — a Components V2 message
    cannot have its components replaced — so the edit failed silently and the
    controls of a command that had just been deleted stayed on screen.
    """
    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message

    await scenario.click(ml(f"buttons.{button_key}.label", locale="pt-br"))
    await scenario.submit_confirmation()

    scenario.expect_message(
        title_contains=ml(f"commands.command-events.{event_key}.title", locale="pt-br"),
    )
    assert panel.deleted, "the panel must leave the screen, not keep its buttons"


async def test_pause_persists_from_the_panel(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps)

    await scenario.click(ml("buttons.pause.label", locale="pt-br"))
    await scenario.submit_confirmation()

    scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"enabled": False}
    )

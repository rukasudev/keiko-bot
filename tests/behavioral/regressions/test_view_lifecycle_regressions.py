"""Regression: a message's buttons stay alive until the message itself leaves.

What broke: discord.py's ViewStore maps message -> item at send time and reads
`item.view` live at dispatch. Button callbacks on the manager did
`parent_view.clear_items()` before opening the next screen, which sets
`item._view = None` on the very objects the store returns. Whenever the next
screen did NOT replace the message (a modal response leaves it untouched; a
failed transition leaves it on screen), every button the user could still see
was dead: each click logged `View interaction referencing unknown view for
item ... Discarding` and Discord showed "This interaction failed".

Exposed by: /moderações bloquear links -> Adicionar. Its composition starts
with a modal, so the panel stays on screen while the modal is open; dismissing
the modal (Discord tells the bot nothing) and clicking Adicionar again hit the
cleared view. The HelpButton had the same divorced mutation and also crashed
on the panel (`interaction.message.embeds[0]` on a message with no embeds).

Shared behavior affected: `app/components/buttons.py` (EditButton,
AddItemButton, RemoveItemButton, HelpButton), consumed by every command's
manager panel.

Must remain guaranteed: after any aborted or failed sub-flow (dismissed modal,
duplicate item, help), every button still visible on screen keeps working.
"""
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.regression,
              pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

BLOCK_LINKS_COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "mode": "block_all",
    "answer": "Nada de links por aqui!",
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "allowed_chats": {"style": "channel", "values": "100"},
    "custom_links": {
        "style": "composition",
        "values": [
            {"link": {"value": "twitch.tv/jway"},
             "match_type": {"value": "domain", "_raw_value": "domain"}},
        ],
    },
}

ADD = ml("buttons.add.label", locale="pt-br")


async def _panel(scenario_factory, deps):
    from app.services.block_links import normalize_block_links_config

    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS_COG))
    return await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(dict(BLOCK_LINKS_COG))
    )


async def test_add_works_again_after_the_modal_is_dismissed(scenario_factory, deps):
    """The reported bug, exactly: Adicionar opens a modal (the panel stays on
    screen), the user closes the modal without submitting — Discord tells the
    bot nothing — and clicks Adicionar again. The second click must open the
    modal again, not die against a cleared view."""
    scenario = await _panel(scenario_factory, deps)

    await scenario.click(ADD)
    scenario.expect_modal()
    scenario.dismiss_modal()

    await scenario.click(ADD)
    scenario.expect_modal()


async def test_rapid_consecutive_clicks_all_reach_a_live_view(scenario_factory, deps):
    """Users double-click. The first click must not leave the view in a state
    where the second one is silently discarded."""
    scenario = await _panel(scenario_factory, deps)

    await scenario.click(ADD)
    await scenario.click(ADD)          # no dismissal in between

    scenario.expect_modal()


async def test_duplicate_add_says_item_and_the_manager_still_works(
    scenario_factory, deps
):
    """The user's scenario end to end: add an item that already exists, get
    the (generic, Keiko-voiced) feedback, and go on using the manager."""
    scenario = await _panel(scenario_factory, deps)

    await scenario.click(ADD)
    await scenario.submit_modal({"Digite o link ou site": "twitch.tv/jway"})
    await scenario.click("option:🌐 Todos os links desse site")

    scenario.expect_error(ml("errors.item-already-registered.message", locale="pt-br"))
    assert "membro" not in (scenario.outputs[-1].get("embed") or {}).get(
        "description", ""
    ), "the shared add flow must not talk about 'member': the item can be anything"

    # The panel was legitimately replaced during the flow; reopening the
    # manager must yield a fully working panel again.
    from app.services.block_links import normalize_block_links_config

    await scenario.start_manager(
        "block_links", normalize_block_links_config(dict(BLOCK_LINKS_COG))
    )
    await scenario.click(ADD)
    scenario.expect_modal()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_duplicate_feedback_copy_exists_in_both_languages(locale):
    for key in ("errors.item-already-registered.title",
                "errors.item-already-registered.message"):
        text = ml(key, locale=locale)
        assert text and not text.startswith("errors."), f"{key} missing for {locale}"
    message = ml("errors.item-already-registered.message", locale=locale)
    assert "membro" not in message and "member" not in message, \
        "the duplicate feedback is shared by every composition command"


async def test_help_describes_the_buttons_without_killing_the_panel(
    scenario_factory, deps
):
    """Help must be a read-only screen: it lists what each button does in its
    own ephemeral message and leaves the panel exactly as it was. The old
    implementation edited the panel (crashes: a Components V2 message has no
    embeds) and then cleared the view, killing every button on screen."""
    scenario = await _panel(scenario_factory, deps)

    await scenario.click(ml("buttons.help.label", locale="pt-br"))

    event = scenario.expect_message(kind="send", ephemeral=True)
    fields = (event.get("embed") or {}).get("fields") or []
    labels = [field.get("name", "") for field in fields]
    assert any(ADD in label for label in labels), \
        f"the help embed lists the panel's buttons, got {labels}"

    await scenario.click(ADD)          # the panel is still fully alive
    scenario.expect_modal()

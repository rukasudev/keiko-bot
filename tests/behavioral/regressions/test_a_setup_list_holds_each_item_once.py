"""A list built during a setup holds each item once, by the key that makes it unique.

Broke as: the form engine's merge of a finished item compared `index != index`, a
loop variable shadowing the parameter it meant, so the check for an item already
listed under the same `unique_by` value never matched. Adding a link the setup
already listed appended it again, and the review and the saved document showed
it twice.

Shared behaviour: the merge of a child session's item into its parent's list
(`merge_item` in the form engine), used by the review's Add and Edit of every
form whose list declares `unique_by` (block_links by link, reminders_birthday by
member). Exposed by: block_links, adding the same website twice before
confirming.

Guaranteed: during a setup, an item whose unique value is already listed takes
that item's place instead of being added again; the review lists it once and the
document the setup saves holds it once. A different value is still added beside
the first.
"""

import discord
import pytest

from tests.behavioral.golden.paths.common import GUILD_ID, label
from tests.behavioral.harness.locators import walk_items

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]

FORM = "block_links"
LOCALE = "en-us"
LINK_FIELD = "Type the link or website"


async def _review_with_one_link(scenario_factory, link):
    scenario = await scenario_factory(locale=LOCALE).start_command(FORM)
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(label("yes", LOCALE))
    await scenario.submit_modal({LINK_FIELD: link})
    await scenario.select_option("general", target="allowed_chats")
    await scenario.confirm()
    scenario.expect_step("confirm")
    return scenario


def _review_text(scenario):
    view = scenario.current_message.view
    return "\n".join(
        item.content
        for item in walk_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    )


def _saved_links(scenario):
    document = scenario.get_persisted("guild", FORM, {"guild_id": GUILD_ID})
    return [item["link"]["value"] for item in document["custom_links"]["values"]]


async def test_a_link_added_again_in_a_setup_takes_the_place_of_the_one_listed(
    scenario_factory,
):
    scenario = await _review_with_one_link(scenario_factory, "meusite.com.br")

    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD: "meusite.com.br"})

    scenario.expect_step("confirm")
    assert _review_text(scenario).count("meusite.com.br") == 1

    await scenario.confirm()

    assert _saved_links(scenario) == ["meusite.com.br"]


async def test_a_different_link_added_in_a_setup_is_listed_beside_the_first(
    scenario_factory,
):
    scenario = await _review_with_one_link(scenario_factory, "meusite.com.br")

    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD: "docs.example.org"})
    await scenario.confirm()

    assert _saved_links(scenario) == ["meusite.com.br", "docs.example.org"]

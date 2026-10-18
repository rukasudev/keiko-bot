"""An item added from the review shows up on the review.

On the local bot (2026-09-16) adding a member with the review's Add button
left the birthday list on the review unchanged: the member was saved, but the
review still listed the first one only.

Shared behaviour: the review screen of every form with a composition.
Consumer that exposed it: reminders_birthday. Guaranteed: after a child
session adds an item, the review lists it.
"""

import discord
import pytest

from tests.behavioral.harness.locators import walk_items
from tests.mocks.discord import create_guild, create_member
from tests.behavioral.golden.paths.common import GUILD_ID, label

pytestmark = pytest.mark.behavioral

DAY_FIELD = {"pt-br": "Dia", "en-us": "Day"}


def _guild():
    guild = create_guild()
    create_member(guild, id=777, name="Amiga", roles=["Member"])
    return guild


def _texts(message):
    texts = [embed.description or "" for embed in message.embeds or []]
    if message.view is not None:
        texts += [
            item.content
            for item in walk_items(message.view)
            if isinstance(item, discord.ui.TextDisplay)
        ]
    return texts


async def _complete_settings_card(scenario):
    await scenario.click("customize:0")
    await scenario.select_option("general")
    await scenario.click("customize:1")
    await scenario.select_option("America/Sao_Paulo")
    await scenario.click("customize:2")
    await scenario.click("08:00")
    await scenario.click("done")


async def _complete_member_card(scenario, locale, month="05", day="12"):
    await scenario.click("customize:0")
    await scenario.select_option(month)
    await scenario.click("customize:1")
    await scenario.submit_modal({DAY_FIELD[locale]: day})
    await scenario.click("done")


async def test_a_member_added_from_the_review_is_listed_on_the_review(
    scenario_factory,
):
    locale = "pt-br"
    scenario = await scenario_factory(locale=locale, guild=_guild()).start(
        "reminders_birthday"
    )
    await scenario.confirm()
    await _complete_settings_card(scenario)
    await scenario.click("Sim")
    await scenario.select_option("Tester")
    await scenario.confirm()
    await _complete_member_card(scenario, locale)
    scenario.expect_step("confirm")

    await scenario.click("add")
    await scenario.select_option("Amiga")
    await scenario.confirm()
    await _complete_member_card(scenario, locale, month="11", day="03")

    scenario.expect_step("confirm")
    joined = "\n".join(_texts(scenario.current_message))
    assert "<@555>" in joined
    assert "<@777>" in joined


async def test_the_first_member_added_from_the_review_is_listed_on_the_review(
    scenario_factory,
):
    """The list was left for later, so the review has no list block yet."""
    locale = "pt-br"
    scenario = await scenario_factory(locale=locale, guild=_guild()).start(
        "reminders_birthday"
    )
    await scenario.confirm()
    await _complete_settings_card(scenario)
    await scenario.click("Depois")
    scenario.expect_step("confirm")

    await scenario.click("add")
    await scenario.select_option("Amiga")
    await scenario.confirm()
    await _complete_member_card(scenario, locale, month="11", day="03")

    scenario.expect_step("confirm")
    joined = "\n".join(_texts(scenario.current_message))
    assert "<@777>" in joined


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


@pytest.mark.regression
@pytest.mark.shared_contract("form_engine")
async def test_a_link_added_again_in_a_setup_takes_the_place_of_the_one_listed(
    scenario_factory,
):
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
    scenario = await _review_with_one_link(scenario_factory, "meusite.com.br")

    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD: "meusite.com.br"})

    scenario.expect_step("confirm")
    assert _review_text(scenario).count("meusite.com.br") == 1

    await scenario.confirm()

    assert _saved_links(scenario) == ["meusite.com.br"]


@pytest.mark.regression
@pytest.mark.shared_contract("form_engine")
async def test_a_different_link_added_in_a_setup_is_listed_beside_the_first(
    scenario_factory,
):
    scenario = await _review_with_one_link(scenario_factory, "meusite.com.br")

    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD: "docs.example.org"})
    await scenario.confirm()

    assert _saved_links(scenario) == ["meusite.com.br", "docs.example.org"]

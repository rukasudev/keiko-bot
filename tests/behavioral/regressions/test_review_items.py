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

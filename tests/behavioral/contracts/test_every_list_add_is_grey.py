"""Contract: the Add of a list is grey wherever a list is drawn.

Round 3 of the beta.2 feedback painted every list's "➕ Adicionar" blue; in
round 4 Lucas asked for it grey (secondary) again: beside the list's title on
the manager panel and on the setup summary, on the birthday pages, and on the
lists a configuration card draws, like every other Edit, Add and Remove.
"""

import discord
import pytest

from app.core.utils import ml
from tests.behavioral.flows import birthdays, block_links, twitch, youtube
from tests.behavioral.flows.common import seed_document
from tests.behavioral.harness import locators

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]


def _adds(scenario, locale):
    add = ml("buttons.add.label", locale=locale)
    return [
        item
        for item in locators.walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.Button) and item.label == add
    ]


def _assert_grey(scenario, locale, where):
    adds = _adds(scenario, locale)
    assert adds, f"{where}: no Add on screen"
    assert {item.style for item in adds} == {discord.ButtonStyle.secondary}, where


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
@pytest.mark.parametrize(
    "paths", [twitch, youtube, block_links], ids=lambda m: m.FORM
)
async def test_the_add_beside_a_list_on_the_panel_is_grey(
    scenario_factory, deps, paths, locale
):
    seed_document(deps, paths.FORM, paths.ENABLED)
    scenario = await scenario_factory(locale=locale).start_command(paths.FORM)

    _assert_grey(scenario, locale, f"{paths.FORM} panel")
    await scenario.finish()


async def test_the_add_of_the_birthday_pages_is_grey(scenario_factory, deps):
    scenario = await birthdays.open_manager(scenario_factory, deps, "pt-br")

    _assert_grey(scenario, "pt-br", "birthday panel")
    await scenario.finish()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_add_of_a_list_on_the_setup_summary_is_grey(
    scenario_factory, deps, locale
):
    scenario = await twitch.open_card(scenario_factory, deps, locale)
    await twitch.pick_channel(scenario)
    await twitch.submit_streamer(scenario, "gaules")
    await twitch.submit_messages(scenario)
    await scenario.click("done")

    _assert_grey(scenario, locale, "twitch setup summary")
    await scenario.finish()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_add_of_a_list_a_card_draws_is_grey(scenario_factory, locale):
    scenario = await block_links.open_card(scenario_factory, locale)
    await scenario.select_option("allow_all", target="mode")

    _assert_grey(scenario, locale, "block links card")
    await scenario.finish()

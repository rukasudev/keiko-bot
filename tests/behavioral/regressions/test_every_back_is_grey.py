"""Regression: every Voltar Keiko draws is grey.

What broke: the Voltar of a /configurar category page was blue (primary)
whenever the page had no page turns beside it, the Voltar of the server
settings screen was always blue, and so was the Voltar of the help, the one
that goes back from a command to the list. The maintainer asked (PR15 round 5,
k02) for every Voltar to be grey (secondary), always, like the form's Back.

Shared behavior affected: the navigation buttons of the /configurar screens
(`category_view` and `settings_screen`, app/services/setup.py), the help's
`BackButton` (app/components/buttons.py, drawn by `HelpSelect`) and the form
platform's Back (`back_button` and the pickers' `picker_back`).

Exposed by: /configurar, on a category with one page.

Must remain guaranteed: any Voltar on any of these screens is secondary.
"""
import discord
import pytest

from app.components.buttons import BackButton
from app.constants import Commands
from app.core.utils import ml
from tests.mocks.database import fake_store
from tests.behavioral.golden.paths import block_links
from tests.behavioral.golden.paths import default_roles
from tests.behavioral.harness.locators import walk_items
from tests.generators.moderations import recorded_server

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("setup_dashboard"),
]

GUILD_ID = "123456789"


def _backs(view, locale):
    back = ml("buttons.back.label", locale)
    return [
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Button) and item.label == back
    ]


def _assert_grey(view, locale, where):
    backs = _backs(view, locale)
    assert backs, f"{where}: no Voltar on screen"
    assert {item.style for item in backs} == {discord.ButtonStyle.secondary}, where


def _rows(count):
    return [(spec, "pending") for spec in Commands.SETUP_FEATURES[:count]]


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
@pytest.mark.parametrize("pages", [1, 2])
def test_the_voltar_of_a_category_page_is_grey(locale, pages):
    from app.services.setup import category_view

    view = category_view("moderations", _rows(2), 0, pages, locale)

    _assert_grey(view, locale, f"category page of {pages} page(s)")


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_voltar_of_the_settings_screen_is_grey(deps, locale):
    from app.data.guild import settings as guild_settings
    from app.core import cache
    from app.services.setup import settings_screen

    recorded_server(fake_store(), GUILD_ID, language=locale)
    cache.reset()

    _assert_grey(await settings_screen(GUILD_ID), locale, "settings screen")


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_the_voltar_of_the_help_is_grey(locale):
    view = discord.ui.View()
    view.add_item(BackButton(view, discord.Embed(), locale))

    _assert_grey(view, locale, "help")


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_back_of_a_form_step_is_grey(scenario_factory, locale):
    scenario = await default_roles._setup(scenario_factory, locale)
    await scenario.select_option("Member", target="default_roles")
    await scenario.click("done")

    _assert_grey(scenario.current_message.view, locale, "default roles, second step")
    await scenario.finish()


async def test_the_back_of_a_card_picker_is_grey(scenario_factory):
    scenario = await block_links._on_card(scenario_factory, "pt-br")
    await scenario.click("customize:2")

    _assert_grey(scenario.current_message.view, "pt-br", "block links exceptions")
    await scenario.finish()

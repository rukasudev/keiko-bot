"""Contract: every page /configurar draws stays inside Discord's limits.

/configurar listed every feature on one card, three components a row, and seven
features already took 38 of the 40 components Discord allows on a message
(PR15 plan, section 1): the eighth feature would have made the whole card fail
to send. The card is now paged by category, `ViewConstants.SETUP_PAGE_SIZE`
features a page, every row its text and the button beside it.

Guaranteed: with thirteen features in one category, every one paused, every
page of that category and the first page stay under forty components and four
thousand characters, in both locales; the page size leaves room to spare; and
every category a feature declares is one /configurar lists, with its emoji.
"""
from itertools import cycle, islice

import pytest

from app.constants import Commands, Feature, ViewConstants
from tests.behavioral.contracts.test_components_v2_limits import (
    MAX_COMPONENTS,
    _assert_within_limits,
)
from tests.behavioral.harness.locators import walk_items
from tests.generators.moderations import recorded_server

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("setup_dashboard")]

GUILD_ID = "123456789"
CROWDED = 13


def speaks(deps, locale):
    """The server's saved language, which every /configurar page is drawn in."""
    from app.core import cache

    recorded_server(deps.mongo_client, GUILD_ID, language=locale)
    cache.reset()


@pytest.fixture
def crowded(monkeypatch):
    """Thirteen features in the moderations category, every one paused."""
    from app.services import setup

    rows = [
        ({**spec, "category": "moderations"}, "paused")
        for spec in islice(cycle(Commands.SETUP_FEATURES), CROWDED)
    ]

    async def states(guild_id):
        return rows

    monkeypatch.setattr(setup, "_states", states)
    return rows


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_every_page_of_a_crowded_category_fits_discord(crowded, deps, locale):
    from app.services.setup import category_page

    speaks(deps, locale)
    pages = -(-CROWDED // ViewConstants.SETUP_PAGE_SIZE)
    counts = []
    for page in range(pages):
        view = await category_page(GUILD_ID, "moderations", page)
        _assert_within_limits(view, f"moderations page {page + 1} {locale}")
        counts.append(len(list(walk_items(view))))

    assert max(counts) <= MAX_COMPONENTS - 5, f"room to spare: {counts}"


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_first_page_of_a_crowded_server_fits_discord(crowded, deps, locale):
    from app.services.setup import setup_dashboard

    speaks(deps, locale)
    _assert_within_limits(await setup_dashboard(GUILD_ID), f"first page {locale}")


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_category_page_a_setup_ends_on_fits_discord(crowded, deps, locale):
    from app.services.setup import category_page

    speaks(deps, locale)
    view = await category_page(
        GUILD_ID, "moderations", activated=Commands.REMINDERS_BIRTHDAY_KEY
    )

    _assert_within_limits(view, f"category page after a setup {locale}")
    assert len(list(walk_items(view))) <= MAX_COMPONENTS - 5


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_settings_screen_fits_discord(deps, locale):
    from app.services.setup import settings_screen

    speaks(deps, locale)
    _assert_within_limits(await settings_screen(GUILD_ID), f"settings {locale}")


def test_every_category_a_feature_declares_is_listed_with_its_emoji():
    declared = {feature.value.group for feature in Feature}

    assert declared <= set(Commands.SETUP_CATEGORIES)
    assert all(Commands.SETUP_CATEGORIES.values())

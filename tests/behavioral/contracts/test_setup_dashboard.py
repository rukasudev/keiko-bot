"""The /setup screen: the first thing most servers see of Keiko.

It was a dense embed: one bold line per feature, the command on the next line,
and a row of buttons far from the lines they belonged to.

Guaranteed: a Components V2 card with Keiko's picture beside the title; one row
per feature with its status, what it does, its command and a button beside it;
the button reads Set up until the feature is configured and Manage after; the
card stays inside Discord's limits in both locales; a row button opens the
feature through the setup_dashboard source; and the greeting's dashboard button
sends the same card.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.constants import Commands, KeikoIcons
from app.services.utils import ml
from tests.behavioral.contracts.test_components_v2_limits import _assert_within_limits
from tests.behavioral.harness.locators import walk_items

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("setup_dashboard")]

GUILD_ID = "123456789"


@pytest.fixture
def configured(deps):
    """Welcome messages enabled, block links paused, the rest never set up."""
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": GUILD_ID,
        Commands.WELCOME_MESSAGES_KEY: True,
        Commands.BLOCK_LINKS_KEY: True,
    })
    deps.mongo_client.guild[Commands.BLOCK_LINKS_KEY].insert_one(
        {"guild_id": GUILD_ID, Commands.ENABLED_KEY: False}
    )


async def dashboard(locale="pt-br"):
    from app.views.setup import setup_dashboard

    return await setup_dashboard(GUILD_ID, locale)


def rows(view):
    return [
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Section)
        and isinstance(item.accessory, discord.ui.Button)
    ]


def text_of(section):
    return "\n".join(
        child.content
        for child in section.children
        if isinstance(child, discord.ui.TextDisplay)
    )


async def test_the_setup_screen_is_a_components_v2_card_with_keiko_on_the_right(
    configured,
):
    view = await dashboard()

    assert isinstance(view, discord.ui.LayoutView)
    pictures = [
        item.media.url
        for item in walk_items(view)
        if isinstance(item, discord.ui.Thumbnail)
    ]
    assert pictures == [KeikoIcons.IMAGE_01]


async def test_every_feature_has_its_own_row_with_a_button_beside_it(configured):
    found = rows(await dashboard())

    assert len(found) == len(Commands.SETUP_FEATURES)
    for section, feature in zip(found, Commands.SETUP_FEATURES):
        text = text_of(section)
        assert ml(f"buttons.setup.{feature['button_key']}.label", "pt-br") in text
        assert ml(f"buttons.setup.{feature['button_key']}.desc", "pt-br") in text
        assert "`/" in text, "the command sits on the row its button opens"


async def test_the_button_reads_set_up_or_manage_from_the_saved_state(configured):
    by_key = {section.accessory.command_key: section for section in rows(await dashboard())}
    start = ml("buttons.setup.start.label", "pt-br")
    manage = ml("buttons.setup.manage.label", "pt-br")

    assert by_key[Commands.DEFAULT_ROLES_KEY].accessory.label == start
    assert by_key[Commands.DEFAULT_ROLES_KEY].accessory.style is discord.ButtonStyle.success
    assert by_key[Commands.WELCOME_MESSAGES_KEY].accessory.label == manage
    assert by_key[Commands.WELCOME_MESSAGES_KEY].accessory.style is discord.ButtonStyle.secondary
    assert by_key[Commands.BLOCK_LINKS_KEY].accessory.label == manage, (
        "a paused feature is still set up"
    )
    assert ml("commands.commands.setup.embed.paused", "pt-br") in text_of(
        by_key[Commands.BLOCK_LINKS_KEY]
    )


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_setup_screen_stays_inside_discord_limits_in_both_locales(
    configured, locale
):
    _assert_within_limits(await dashboard(locale), f"/setup {locale}")


async def test_a_row_button_opens_the_feature_through_the_setup_dashboard_source(
    configured, monkeypatch
):
    from app.components import buttons

    opened = AsyncMock()
    monkeypatch.setattr(buttons, "run_feature_command", opened)
    button = rows(await dashboard())[1].accessory
    interaction = SimpleNamespace()

    await button.callback(interaction)

    opened.assert_awaited_once_with(
        interaction, Commands.DEFAULT_ROLES_KEY, "setup_dashboard"
    )


async def test_the_greeting_dashboard_button_sends_the_same_screen(configured):
    from app.views.greetings import DashboardButton

    sent = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True)),
        locale=discord.Locale.brazil_portuguese,
        guild=SimpleNamespace(id=int(GUILD_ID)),
        response=SimpleNamespace(send_message=sent),
    )

    await DashboardButton(label="Configurar", locale="pt-br").callback(interaction)

    kwargs = sent.await_args.kwargs
    assert isinstance(kwargs["view"], discord.ui.LayoutView)
    assert kwargs.get("embed") is None, "a Components V2 message carries no embed"

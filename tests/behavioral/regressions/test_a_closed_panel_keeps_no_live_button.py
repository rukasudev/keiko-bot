"""Regression: a closed panel keeps its text and no button anyone can click.

What broke: once a manager panel closed (it expired, or another panel deleted
the feature while it stayed open), the message lost its rows of buttons but
kept every button that sits beside a block of text: the Edit of each heading
and the Add beside a list's title. Discord still drew them and still sent
their clicks, so the closed panel looked usable and answered a click with the
expired notice.

Shared behavior affected: `finalized_layout` (app/forms/discord/views.py),
which `closed_payload` and `Executor._strip_controls`
(app/forms/discord/transitions.py) run on every closed, expired, discarded
and previewed Components V2 message of every form.

Exposed by: block_links, whose panel draws an Edit beside each heading and an
Add beside the custom links.

Must remain guaranteed: a panel that expires, and a heading's screen closed
because another panel deleted the feature, keep the text they showed (Keiko's
picture beside the title included) and register no button and no select, but
the expired one's button that opens the feature again (`ReopenButton`).
"""
from datetime import datetime, timezone

import discord
import pytest

from app.core.utils import ml
from app.forms.discord.views import ReopenButton
from app.forms.features import feature_for
from app.forms.features.feature import CommitContext
from tests.behavioral.golden.paths import block_links
from tests.behavioral.golden.paths.common import GUILD_ID, seed_document
from tests.mocks.discord import create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]

LOCALE = "pt-br"
CONTROLS = (discord.ui.Button, discord.ui.Select, discord.ui.ChannelSelect,
            discord.ui.RoleSelect, discord.ui.UserSelect)


def _clickable(message):
    return [item for item in message.registered_items if isinstance(item, CONTROLS)]


def _texts(message):
    return "\n".join(
        item.content for item in message.registered_items
        if isinstance(item, discord.ui.TextDisplay)
    )


def _thumbnails(message):
    return [
        item for item in message.registered_items
        if isinstance(item, discord.ui.Thumbnail)
    ]


async def _delete_elsewhere():
    context = CommitContext(
        GUILD_ID, "556", LOCALE, block_links.ENABLED,
        datetime(2026, 1, 1, 12, tzinfo=timezone.utc), "manager", "other",
    )
    await feature_for(block_links.FORM).commit("disable", {}, context)


async def test_an_expired_panel_keeps_its_text_and_no_button(scenario_factory, deps):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    scenario = await scenario_factory(locale=LOCALE).start_command(block_links.FORM)
    panel = scenario.current_message
    assert _clickable(panel), "the open panel has its buttons"

    await scenario.expire()

    assert _clickable(panel) == [], "no Edit or Add beside a heading stays live"
    reopens = [item for item in panel.registered_items if isinstance(item, ReopenButton)]
    assert len(reopens) == 1, "only the button that opens the feature again"
    shown = _texts(panel)
    assert "### Configurações Gerais" in shown
    assert "Links personalizados permitidos" in shown
    assert ml("texts.notices.expired", locale=LOCALE) in shown
    assert _thumbnails(panel), "Keiko's picture stays beside the title"


async def test_a_screen_closed_by_another_panels_delete_keeps_no_button(
    scenario_factory, deps,
):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    guild = create_guild()
    admin = create_member(guild, id=555, name="Tester")
    scenario = await scenario_factory(
        locale=LOCALE, guild=guild, user=admin
    ).start_command(block_links.FORM)
    await scenario.click("edit:group:exceptions")
    screen = scenario.current_message
    assert _clickable(screen)

    await _delete_elsewhere()
    await scenario.click("Spotify")

    assert _clickable(screen) == [], "the list's Add beside its title is gone too"
    shown = _texts(screen)
    assert "Links personalizados permitidos" in shown
    assert ml("texts.notices.closed", locale=LOCALE) in shown

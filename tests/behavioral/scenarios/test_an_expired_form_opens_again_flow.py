"""Behavioral scenarios: an expired form offers one button that opens it again.

The expired line says the admin can call the command again or press the button
below it; the button is the only control left on the message. Its custom id is
the form platform's own (`k:<session>:<revision>:reopen:<feature>`), and
discord.py routes it by its template (`ReopenButton`, registered once at
startup), so it works after the runtime forgot the session and after a restart.
It opens the feature the way its slash command does: the panel when the
feature is configured, its setup when it is not, for an admin only.
"""

import discord
import pytest

from app.core.utils import ml
from app.forms.discord.callbacks import RUNTIME
from app.forms.discord.interactions import decode, encode
from app.forms.discord.views import ReopenButton
from tests.behavioral.golden.paths import block_links
from tests.behavioral.golden.paths.common import seed_document
from tests.behavioral.harness import locators
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

LOCALE = "pt-br"
EXPIRED_LINE = ml("texts.notices.expired", locale=LOCALE)
CONTROLS = (
    discord.ui.Button,
    discord.ui.Select,
    discord.ui.ChannelSelect,
    discord.ui.RoleSelect,
    discord.ui.UserSelect,
)


def _scenario(scenario_factory, admin=True):
    guild = create_guild()
    member = create_member(guild, id=555, name="Tester")
    member.guild_permissions = discord.Permissions(administrator=admin)
    return scenario_factory(locale=LOCALE, guild=guild, user=member)


def _reopen_of(message) -> ReopenButton:
    items = list(locators.walk_items(message.view))
    reopens = [item for item in items if isinstance(item, ReopenButton)]
    others = [item for item in items if isinstance(item, CONTROLS)]
    assert len(reopens) == 1 and others == [], "the button is the only control left"
    return reopens[0]


async def _routed(scenario, message, custom_id):
    """The click as discord.py routes it once no view holds the message any more."""
    match = ReopenButton.__discord_ui_compiled_template__.fullmatch(custom_id)
    assert match is not None
    interaction = scenario._mint(message=message, custom_id=custom_id)
    drawn = discord.ui.Button(label="Abrir de novo", custom_id=custom_id)
    item = await ReopenButton.from_custom_id(interaction, drawn, match)
    await item.callback(interaction)


async def test_an_expired_setup_opens_its_setup_again(scenario_factory, deps):
    scenario = await _scenario(scenario_factory).start_command("default_roles")
    message = scenario.current_message

    await scenario.expire()

    assert ml("texts.notices.expired", locale=LOCALE) in message.embeds[0].description
    reopen = _reopen_of(message)
    assert reopen.item.label == ml("buttons.reopen.label", locale=LOCALE)
    assert reopen.feature == "default_roles"

    RUNTIME.reset()
    await _routed(scenario, message, reopen.custom_id)

    assert scenario.current_message is not message, "a new message, the setup's"
    assert scenario.session is not None and scenario.session.key == "default_roles"
    assert scenario.session.mode.kind == "setup"


async def test_an_expired_panel_opens_its_panel_again(scenario_factory, deps):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    scenario = await _scenario(scenario_factory).start_command(block_links.FORM)
    panel = scenario.current_message

    await scenario.expire()

    shown = "\n".join(
        item.content
        for item in panel.registered_items
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert shown.endswith(ml("texts.notices.expired", locale=LOCALE))
    reopen = _reopen_of(panel)

    RUNTIME.reset()
    await _routed(scenario, panel, reopen.custom_id)

    assert scenario.current_message is not panel
    assert scenario.session.key == block_links.FORM
    assert scenario.session.mode.kind == "manage", "a configured feature opens its panel"


async def test_only_an_admin_opens_an_expired_form_again(scenario_factory, deps):
    scenario = await _scenario(scenario_factory, admin=False).start_command(
        "default_roles"
    )
    message = scenario.current_message
    await scenario.expire()
    reopen = _reopen_of(message)
    RUNTIME.reset()

    await _routed(scenario, message, reopen.custom_id)

    told = scenario.outputs[-1]
    assert told["embed"]["title"] == ml("commands.responses.admin_only.title", LOCALE)
    assert RUNTIME.store._sessions == {}, "nothing opened"


def test_the_template_routes_only_the_reopen_button():
    template = ReopenButton.__discord_ui_compiled_template__

    assert template.fullmatch(encode("abc", 3, "reopen", "block_links"))
    assert not template.fullmatch(encode("abc", 3, "lifecycle", "pause"))
    assert not template.fullmatch(encode("abc", 3, "cancel"))
    assert decode(encode("abc", 3, "reopen", "block_links")).arg == "block_links"


async def test_a_feature_that_no_longer_exists_answers_closed(scenario_factory, deps):
    """A message drawn before a feature was removed keeps its button: the click
    answers that the form is closed instead of failing."""
    scenario = _scenario(scenario_factory)
    interaction = scenario._mint()

    await RUNTIME.reopen(interaction, "gone_feature")

    told = scenario.outputs[-1]
    assert told["content"] == ml("texts.notices.closed", locale=LOCALE)
    assert told["ephemeral"] is True


async def test_a_late_click_with_no_message_sends_the_button_with_its_line(
    scenario_factory, deps
):
    """With no message to edit, the expired line goes out on a message of its own,
    with the button it names."""
    from app.forms.discord.interactions import encode

    scenario = _scenario(scenario_factory)
    interaction = scenario._mint()

    await RUNTIME.late(
        interaction, encode("block_links-0123456789abcdef", 1, "lifecycle", "pause")
    )

    sent = scenario.outputs[-1]
    assert sent["ephemeral"] is True
    assert sent["embed"]["description"].endswith(EXPIRED_LINE)
    reopens = [
        item for item in scenario.current_message.registered_items
        if isinstance(item, ReopenButton)
    ]
    assert [button.feature for button in reopens] == ["block_links"]

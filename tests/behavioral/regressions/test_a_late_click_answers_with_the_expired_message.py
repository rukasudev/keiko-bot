"""Regression: a click on a form whose view is gone answers with the expired message.

What broke: a form's buttons and selects reach the bot only through the view
discord.py holds for the message, and that view goes when it times out with the
session (`ViewConstants.LONG_TIMEOUT_SECONDS`) or when the bot restarts. The
sweep cannot draw the expiry either, past Discord's 15-minute interaction token.
So a click on a form left open got no answer at all ("Esta interação falhou"),
and a modal submitted after its form expired was dropped the same way.

Shared behavior affected: the routing of every component and modal of the form
platform (`app/forms/discord/views.py`, `callbacks.py`, the modal listener in
`app/core/events.py`), for every form.

Exposed by: the owner's test of s13 (a panel left open past its expiry).

Must remain guaranteed: a button, a select, a modal-opening button or a modal
submit that lands after the form expired, after its view timed out or after a
restart edits that message, with that click, to the expired line and the
"Abrir de novo" button; a click while the session is alive is left to its live
view, and gets one answer.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest

from app.constants import DiscordLimits
from app.core.utils import ml
from app.forms.discord.callbacks import RUNTIME
from tests.behavioral.golden.paths import block_links
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths.common import seed_document
from tests.behavioral.harness import locators

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]

LOCALE = "pt-br"
EXPIRED = ml("texts.notices.expired", locale=LOCALE)
CONTROLS = (
    discord.ui.Button,
    discord.ui.Select,
    discord.ui.ChannelSelect,
    discord.ui.RoleSelect,
    discord.ui.UserSelect,
)
PAST_EVERY_DEADLINE = datetime.max.replace(tzinfo=timezone.utc)


def _routes():
    """The dynamic items the bot registers: what discord.py tries on a click no
    view of the message holds."""
    return [
        factory
        for factory in discord.ui.DynamicItem.__subclasses__()
        if factory.__module__.startswith("app.")
    ]


async def _late(scenario, message, item, interaction=None):
    """The click as discord.py routes it once no view holds the message."""
    custom_id = item.custom_id
    interaction = interaction or scenario._mint(message=message, custom_id=custom_id)
    for factory in _routes():
        match = factory.__discord_ui_compiled_template__.fullmatch(custom_id)
        if match is None:
            continue
        rebuilt = discord.ui.LayoutView.from_message(message, timeout=None)
        base = next(
            found
            for found in rebuilt.walk_children()
            if getattr(found, "custom_id", None) == custom_id
        )
        dynamic = await factory.from_custom_id(interaction, base, match)
        await dynamic.callback(interaction)
    return interaction


def _item(message, kind, custom_id_part=""):
    return next(
        item
        for item in message.registered_items
        if isinstance(item, kind) and custom_id_part in (item.custom_id or "")
    )


def _shows_the_expired_message(message, feature):
    shown = "\n".join(
        item.content
        for item in message.registered_items
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert shown.endswith(EXPIRED), shown[-200:]
    assert [i for i in message.registered_items if isinstance(i, CONTROLS)] == []
    reopens = [
        item
        for item in message.registered_items
        if type(item).__name__ == "ReopenButton"
    ]
    assert [button.feature for button in reopens] == [feature]


async def _panel(scenario_factory, deps):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    return await scenario_factory(locale=LOCALE).start_command(block_links.FORM)


def _token_long_gone(monkeypatch):
    """The sweep runs past Discord's interaction token, as it always does live."""
    monkeypatch.setattr(DiscordLimits, "INTERACTION_TOKEN_SECONDS", 0)


async def _twitch_card(scenario_factory, deps):
    scenario = await twitch._card(scenario_factory, deps, LOCALE)
    await twitch._channel(scenario)
    return scenario


async def test_a_button_after_a_restart_answers_with_the_expired_message(
    scenario_factory, deps
):
    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message
    pause = _item(panel, discord.ui.Button, ":lifecycle:pause")
    RUNTIME.reset()

    interaction = await _late(scenario, panel, pause)

    assert interaction.response.is_done(), "the click got an answer"
    _shows_the_expired_message(panel, block_links.FORM)


async def test_a_select_after_its_form_expired_answers_with_the_expired_message(
    scenario_factory, deps, monkeypatch
):
    _token_long_gone(monkeypatch)
    scenario = await _twitch_card(scenario_factory, deps)
    card = scenario.current_message
    channel = _item(card, discord.ui.ChannelSelect)
    await RUNTIME.sweep(PAST_EVERY_DEADLINE)
    assert _item(card, discord.ui.ChannelSelect), "the sweep could not draw it"

    await _late(scenario, card, channel)

    _shows_the_expired_message(card, twitch.FORM)


async def test_a_button_that_opens_a_modal_answers_without_the_modal(
    scenario_factory, deps
):
    scenario = await _twitch_card(scenario_factory, deps)
    card = scenario.current_message
    streamer = locators.find_button(card, "customize:1", scenario.locale)
    RUNTIME.reset()

    await _late(scenario, card, streamer)

    assert scenario.store.pending_modal is None, "no modal opens on a dead form"
    _shows_the_expired_message(card, twitch.FORM)


async def test_a_click_past_the_deadline_before_the_sweep_answers_the_same(
    scenario_factory, deps, monkeypatch
):
    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message
    pause = _item(panel, discord.ui.Button, ":lifecycle:pause")
    monkeypatch.setattr(
        "app.forms.discord.callbacks._now", lambda: PAST_EVERY_DEADLINE
    )

    await _late(scenario, panel, pause)

    _shows_the_expired_message(panel, block_links.FORM)


async def test_a_modal_submitted_after_its_form_expired_answers_the_same(
    scenario_factory, deps, monkeypatch
):
    from app.core.events import Events

    _token_long_gone(monkeypatch)
    scenario = await _twitch_card(scenario_factory, deps)
    card = scenario.current_message
    await scenario.click("customize:1")
    modal = scenario.store.pending_modal
    await RUNTIME.sweep(PAST_EVERY_DEADLINE)
    submitted = scenario._mint(message=card, custom_id=modal.custom_id)
    submitted.type = discord.InteractionType.modal_submit

    await Events(SimpleNamespace()).on_interaction(submitted)

    assert submitted.response.is_done(), "the submit got an answer"
    _shows_the_expired_message(card, twitch.FORM)


async def test_a_click_on_a_live_form_is_left_to_its_view(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message
    pause = _item(panel, discord.ui.Button, ":lifecycle:pause")
    interaction = scenario._mint(message=panel, custom_id=pause.custom_id)
    before = len(scenario.outputs)

    await _late(scenario, panel, pause, interaction)

    assert len(scenario.outputs) == before, "the late route answers nothing"
    await locators.dispatch_click(panel.view, pause, interaction)
    assert scenario.current_message is panel
    assert f"({ml('texts.paused_suffix', locale=LOCALE)})" in str(scenario.outputs)


async def test_a_click_both_routes_reach_is_answered_once(scenario_factory, deps):
    """discord.py hands a click to every dynamic item its custom id matches and to
    the live view that holds it: the first route claims the click, the other
    leaves it, so a form past its deadline is never answered twice."""
    import asyncio

    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message
    pause = _item(panel, discord.ui.Button, ":lifecycle:pause")
    interaction = scenario._mint(message=panel, custom_id=pause.custom_id)
    RUNTIME.reset()

    await asyncio.gather(
        _late(scenario, panel, pause, interaction),
        locators.dispatch_click(panel.view, pause, interaction),
    )

    answers = [event for event in scenario.outputs if event.get("actor") == "bot"]
    assert answers[-1]["kind"] == "edit" and answers[-2]["kind"] != "edit"
    _shows_the_expired_message(panel, block_links.FORM)


def test_the_bot_routes_every_dynamic_item_of_the_forms():
    import inspect

    from app.bot import DiscordBot
    from app.forms.discord.views import routed_items

    declared = {
        factory
        for factory in discord.ui.DynamicItem.__subclasses__()
        if factory.__module__.startswith("app.forms")
    }

    assert set(routed_items()) == declared
    assert "*routed_items()" in inspect.getsource(DiscordBot.setup_hook)


async def test_an_expired_message_never_drops_the_reopen_route():
    """discord.py unregisters the template of every dynamic item a removed view
    holds (`ViewStore.remove_view`, run when a view times out), so one expired
    message whose view timed out would stop every Abrir de novo from routing.
    The expired message's view never times out, and holds no control discord.py
    keeps: nothing of it stays in memory."""
    from discord.ui.view import ViewStore

    from app.forms.discord.transitions import Surface, closed_payload
    from app.forms.discord.views import Ids, ReopenButton, reopen_button, routed_items

    ids = Ids("block_links-0123456789abcdef", 3)
    embed_surface = Surface(embed=discord.Embed(description="painel"))
    expired = closed_payload(embed_surface, "expired", LOCALE, "block_links", ids)
    view = expired["view"]

    assert view.timeout is None
    assert [
        item
        for item in view.walk_children()
        if item.is_dispatchable() and not isinstance(item, discord.ui.DynamicItem)
    ] == []

    store = ViewStore(SimpleNamespace())
    store.add_dynamic_items(*routed_items())
    live = discord.ui.View()
    live.add_item(discord.ui.Button(label="Pausar", custom_id=ids("lifecycle", "pause")))
    timing_out = discord.ui.View(timeout=1)
    timing_out.add_item(reopen_button("block_links", ids, "Abrir de novo"))
    store.add_view(live, 1)
    store.add_view(timing_out, 1)

    store.remove_view(timing_out)

    template = ReopenButton.__discord_ui_compiled_template__
    assert template not in store._dynamic_items, "why the expired view never times out"

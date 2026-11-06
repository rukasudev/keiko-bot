"""Every interaction gets one first answer, and a card cannot be clicked while it loads.

Broke as (beta.2, B5): Discord marks an interaction answered only once the
answer's round trip returns (discord.py 2.7 sets `is_done()` after the HTTP
call). The late-answer clock woke at 1.5 s, read `is_done()` while the first
answer was still on the wire and deferred again, so whichever arrived second
got 40060 ("Could not answer in time"): a Twitch Disable whose commit took
2.5 s was counted failed. After the StreamElements streamer modal, the intro's
buttons stayed clickable during the four lookups; a second click waited for
the lock, was refused as stale, and its notice raced its own clock and lost
with 40060, dropping the redraw that should have followed.

Shared behaviour: the first answer of an interaction (`Answering` in
`app/settings/discord/transitions.py`, used by the executor and the runtime's
clock in `callbacks.py`), the hold that disables a card while the adapter
waits, and the stale-click answer of `settle` (`app/settings/form/form.py`).
Exposed by: notifications_twitch (Disable), stream_elements_commands (setup).

Guaranteed: the clock never sends a second first answer, a card's buttons are
disabled while a lookup runs, and a click that arrived meanwhile is answered
with the current screen only.
"""

import asyncio
import logging

import discord
import pytest

from app.services.utils import ml
from app.settings.features.feature import GenericCogFeature
from app.settings.features.stream_elements_commands import StreamElementsFeature
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths import stream_elements_commands as stream_elements
from tests.behavioral.golden.paths.common import seed_document
from tests.behavioral.harness import locators

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]


def _interactions(outcome, code=""):
    from prometheus_client import REGISTRY

    labels = {"outcome": outcome, "code": code}
    return REGISTRY.get_sample_value("keiko_interactions_total", labels) or 0.0


def _components(message):
    return [
        item
        for item in locators.walk_items(message.view)
        if isinstance(item, (discord.ui.Button, discord.ui.Select,
                             discord.ui.ChannelSelect, discord.ui.RoleSelect,
                             discord.ui.UserSelect))
    ]


async def _until(condition, tries=200):
    for _ in range(tries):
        if condition():
            return True
        await asyncio.sleep(0.005)
    return condition()


@pytest.fixture
def gated_lookup(monkeypatch):
    released = asyncio.Event()
    started = asyncio.Event()
    original = StreamElementsFeature.prefetch

    async def gated(self, *args):
        started.set()
        await released.wait()
        return await original(self, *args)

    monkeypatch.setattr(StreamElementsFeature, "prefetch", gated)
    return started, released


async def test_a_commit_that_ends_as_the_clock_runs_out_is_answered_once(
    scenario_factory, deps, monkeypatch, caplog
):
    seed_document(deps, twitch.FORM, twitch.ENABLED)
    twitch._known_streamers(deps)
    scenario = await scenario_factory().start_command(twitch.FORM)
    monkeypatch.setattr("app.constants.ViewConstants.DEFER_AFTER_SECONDS", 0.05)
    committing = GenericCogFeature.commit

    async def slow(self, kind, payload, context):
        await asyncio.sleep(0.04)
        return await committing(self, kind, payload, context)

    monkeypatch.setattr(GenericCogFeature, "commit", slow)
    scenario.latency = 0.1
    refused = _interactions("failed", "40060")

    with caplog.at_level(logging.WARNING):
        await scenario.click("disable")
        await scenario.submit_confirmation()
        await asyncio.sleep(0.2)

    assert _interactions("failed", "40060") == refused
    assert "Could not answer in time" not in caplog.text
    disabled = ml("commands.command-events.disabled.title", locale="pt-br")
    scenario.expect_message(title_contains=disabled.split("$")[0].strip())


async def test_the_card_cannot_be_clicked_while_a_modal_lookup_runs(
    scenario_factory, deps, gated_lookup
):
    started, released = gated_lookup
    stream_elements._known_streamers(deps)
    scenario = await scenario_factory().start_command(stream_elements.FORM)
    await scenario.confirm()
    field = scenario.pending_modal_fields()[0]

    submit = asyncio.ensure_future(scenario.submit_modal({field: "shroud"}))
    await asyncio.wait_for(started.wait(), 1)
    shown = scenario.current_message
    clickable = [item.custom_id for item in _components(shown) if not item.disabled]
    released.set()
    await submit

    assert _components(shown), "the intro shows its buttons"
    assert clickable == [], f"still clickable while looking up: {clickable}"
    scenario.expect_step("confirm")
    assert not any(item.disabled for item in _components(scenario.current_message))


async def test_a_click_that_arrived_during_the_lookup_is_answered_quietly(
    scenario_factory, deps, gated_lookup, monkeypatch
):
    started, released = gated_lookup
    stream_elements._known_streamers(deps)
    scenario = await scenario_factory().start_command(stream_elements.FORM)
    intro = scenario.current_message
    confirm = locators.find_button(intro, "continue", scenario.locale)
    await scenario.confirm()
    monkeypatch.setattr("app.constants.ViewConstants.DEFER_AFTER_SECONDS", 0.05)
    scenario.latency = 0.02
    field = scenario.pending_modal_fields()[0]

    submit = asyncio.ensure_future(scenario.submit_modal({field: "shroud"}))
    await asyncio.wait_for(started.wait(), 1)
    late = scenario._mint(message=intro, custom_id=confirm.custom_id)
    click = asyncio.ensure_future(confirm.callback(late))
    await asyncio.sleep(0.1)
    released.set()
    await asyncio.gather(submit, click)

    stale = ml("commands.form-notices.stale", locale="pt-br")
    notices = [event for event in scenario.outputs if event.get("content") == stale]
    assert notices == [], "a click that arrived while loading gets no notice"
    scenario.expect_step("confirm")
    assert scenario.current_message.flags.components_v2
    assert scenario.outputs[-1]["kind"] in ("edit", "followup_edit")

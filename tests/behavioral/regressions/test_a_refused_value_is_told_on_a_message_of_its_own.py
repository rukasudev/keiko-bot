"""A refused value is told on a message of its own, below the screen.

Broke as (beta.2 round 2, R3): round 1 drew every validation error inside the
screen that asked for it, a `🚨` line under the card section, under the card's
header or on the one-field card of a modal step. On a phone the admin had to
scroll up to find why the value was refused ("ficou ruim, volte pra mensagem
separada"). The error is a separate ephemeral message again, as before beta.2,
and what round 1 got right stays: the modal opens again with what was typed.

Shared behaviour: `Form.refuse` (`app/settings/form/form.py`), the answer of
every form to a refusal, and the card and modal renderers that no longer draw
it. Exposed by: notifications_twitch (a streamer Twitch does not know),
stream_elements_commands (a modal step), reminders_birthday (Done with fields
missing).

Guaranteed: the refusal reaches the admin as a separate message, the screen
in front of them carries no `🚨` line, and the retry modal is prefilled.
"""

import discord
import pytest

from app.services.utils import ml
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths import stream_elements_commands as streamelements
from tests.behavioral.harness import locators

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]


def _screen_texts(scenario) -> list[str]:
    message = scenario.current_message
    return [
        item.content or ""
        for item in locators.walk_items(message.view)
        if isinstance(item, discord.ui.TextDisplay)
    ]


def _sent(scenario, wanted: str) -> list[dict]:
    found = []
    for event in scenario.outputs:
        embed = event.get("embed") or {}
        text = " ".join(
            filter(None, [event.get("content"), embed.get("title"), embed.get("description")])
        )
        if wanted in text:
            found.append(event)
    return found


def _typed(scenario) -> list:
    return [field.default for field in scenario.store.pending_modal.children]


async def test_an_unknown_streamer_is_told_apart_from_the_card(scenario_factory, deps):
    scenario = await twitch._card(scenario_factory, deps, "pt-br")

    await twitch._submit_streamer(scenario, "naoexiste")

    message = ml("errors.streamer-not-found.message", locale="pt-br")
    errors = _sent(scenario, message)
    assert errors, "the admin never read why the name was refused"
    assert (errors[-1].get("embed") or {}).get("color") == 0xFF0000
    assert errors[-1]["delete_after"] == 10
    assert not any("🚨" in text for text in _screen_texts(scenario))

    await scenario.click("customize:1")
    assert _typed(scenario) == ["naoexiste"], "the modal keeps what was typed"


async def test_a_refused_modal_step_is_told_apart_and_reopens_prefilled(
    scenario_factory, deps,
):
    streamelements._known_streamers(deps)
    scenario = await scenario_factory(locale="pt-br").start_command(
        streamelements.FORM
    )
    await scenario.confirm()
    await streamelements._submit_streamer(scenario, "naoexiste")

    assert _sent(scenario, ml("errors.streamer-not-found.message", locale="pt-br"))
    texts = _screen_texts(scenario)
    assert any("naoexiste" in text for text in texts), texts
    assert not any("🚨" in text for text in texts), texts

    await scenario.click("customize:0")
    assert _typed(scenario) == ["naoexiste"]


async def test_done_with_fields_missing_is_told_apart_from_the_card(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await scenario.click("done")

    required = ml("buttons.summary-card.required", "pt-br").split(":")[0]
    errors = _sent(scenario, required)
    assert errors and errors[-1].get("content"), "a plain sentence of its own"
    assert not any("🚨" in text for text in _screen_texts(scenario))

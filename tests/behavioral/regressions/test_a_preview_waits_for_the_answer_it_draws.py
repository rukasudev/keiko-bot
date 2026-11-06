"""A preview is drawn only once the card holds the answer it is drawn from.

Lucas, on beta.2 (2026-11-06): Pré-visualizar on the Twitch card with no
streamer typed drew a notification for nobody, and the YouTube card did the
same with no youtuber. It must answer "você precisa escolher o streamer"
instead, on a message of its own like every other refusal, and draw nothing.

Shared behaviour: the card's Preview aside (`aside:preview`, the engine's
`Aside` handler in `app/settings/form/form.py`) and the card's
`preview_needs` declaration. Consumers that exposed it: notifications_twitch
and notifications_youtube_video, whose item card previews the announcement.
Guaranteed: while a key the preview needs is empty, Preview sends that
declared error alone and the card on screen stays as it was; once it is
filled, in the setup or in an item edit, Preview draws the announcement.
"""
import json

import pytest

from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths import notifications_youtube_video as youtube
from tests.behavioral.golden.paths.common import open_manager

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]

TWITCH_ERROR = {
    "pt-br": (
        "Ops! Você precisa escolher o streamer antes de eu mostrar a "
        "pré-visualização. Escolha um no card e tente de novo! 🐶"
    ),
    "en-us": (
        "Oops! You need to pick the streamer before I can show the preview. "
        "Pick one on the card and try again! 🐶"
    ),
}
YOUTUBE_ERROR = {
    "pt-br": (
        "Ops! Você precisa escolher o youtuber antes de eu mostrar a "
        "pré-visualização. Escolha um no card e tente de novo! 🐶"
    ),
    "en-us": (
        "Oops! You need to pick the YouTuber before I can show the preview. "
        "Pick one on the card and try again! 🐶"
    ),
}


def _after(scenario, seen):
    return [
        event for event in scenario.outputs[seen:]
        if event["actor"] == "bot" and event["kind"] != "defer"
    ]


def _shown(events):
    return json.dumps(events, ensure_ascii=False)


async def _press_preview(scenario):
    message, state = scenario.current_message, dict(scenario.card_state)
    seen = len(scenario.outputs)
    await scenario.click("preview")
    return message, state, _after(scenario, seen)


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_twitch_card_with_no_streamer_answers_instead_of_previewing(
    scenario_factory, deps, locale
):
    scenario = await twitch._card(scenario_factory, deps, locale)
    await twitch._channel(scenario)

    message, state, events = await _press_preview(scenario)

    assert len(events) == 1, scenario.transcript
    assert events[0]["kind"] in ("send", "followup_send"), scenario.transcript
    assert events[0].get("content") == TWITCH_ERROR[locale], scenario.transcript
    assert "embed" not in events[0], "no preview drawn"
    assert scenario.current_message is message, "the card stays as it is"
    assert dict(scenario.card_state) == state
    await scenario.finish()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_youtube_card_with_no_youtuber_answers_instead_of_previewing(
    scenario_factory, deps, locale
):
    scenario = await youtube._card(scenario_factory, deps, locale)

    message, state, events = await _press_preview(scenario)

    assert len(events) == 1, scenario.transcript
    assert events[0].get("content") == YOUTUBE_ERROR[locale], scenario.transcript
    assert "embed" not in events[0], "no preview drawn"
    assert scenario.current_message is message, "the card stays as it is"
    assert dict(scenario.card_state) == state
    await scenario.finish()


async def test_the_twitch_card_previews_once_the_streamer_is_typed(
    scenario_factory, deps
):
    scenario = await twitch._card(scenario_factory, deps, "pt-br")
    await twitch._submit_streamer(scenario, "gaules")

    _, _, events = await _press_preview(scenario)

    assert TWITCH_ERROR["pt-br"] not in _shown(events)
    assert "Descrição da live aqui" in _shown(events), scenario.transcript
    await scenario.finish()


async def test_an_item_edit_previews_the_youtuber_it_already_holds(
    scenario_factory, deps
):
    scenario = await open_manager(
        scenario_factory, deps, "pt-br", youtube.FORM, youtube._seed
    )
    await scenario.click("section:notifications$0")

    _, _, events = await _press_preview(scenario)

    assert YOUTUBE_ERROR["pt-br"] not in _shown(events)
    assert "Descrição do vídeo aqui" in _shown(events), scenario.transcript
    await scenario.finish()

"""Behavioral scenarios: a mention typed in a notice or birthday message goes on save.

Who a notice or a birthday post pings is the card's 📣 Mencionar. An @everyone,
an @here or a role an admin types in the message itself is taken out of the text
as the modal is saved, through the `without_group_mentions` normalizer the forms
declare on the inputs of those messages, and Keiko's message "📣 Tirei a menção
do texto!", gone after a few seconds like the pause's, says where the mention
belongs. A member's mention stays: the field cannot name one.
"""

import pytest

from app.constants import Style, ViewConstants
from app.core.utils import ml
from app.forms.form.form_yaml import CardStep, ListStep, registry
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths import reminders_birthday as birthdays

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

LOCALE = "pt-br"
MESSAGES = "customize:4"
DEFAULT_MESSAGE = "customize:5"


def _told(scenario):
    title = ml("commands.responses.mention_removed.title", locale=LOCALE)
    return [
        event
        for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == title
    ]


async def _twitch_card(scenario_factory, deps):
    scenario = await twitch._card(scenario_factory, deps, LOCALE)
    await twitch._channel(scenario)
    await twitch._submit_streamer(scenario, "gaules")
    return scenario


async def _type_messages(scenario, *messages):
    await scenario.click(MESSAGES)
    fields = scenario.pending_modal_fields()
    await scenario.submit_modal(dict(zip(fields, messages)))


async def test_a_notice_saves_its_messages_without_the_mentions_typed_in_them(
    scenario_factory, deps
):
    scenario = await _twitch_card(scenario_factory, deps)

    await _type_messages(
        scenario,
        "@everyone {streamer} ao vivo <@&201>",
        "<@777>, olha o {streamer}: {stream_link}",
        "@here",
    )

    assert scenario.card_state["notification_messages"] == (
        "{streamer} ao vivo;<@777>, olha o {streamer}: {stream_link}"
    ), "@everyone, @here and the role go; the member's mention stays"
    [told] = _told(scenario)
    assert told["embed"]["title"] == "📣 Tirei a menção do texto!"
    assert told["embed"]["color"] == int(Style.WARNING_COLOR, 16), "Keiko's yellow"
    assert told["embed"]["description"].startswith(
        ml("commands.responses.mention_removed.description", locale=LOCALE)
    )
    assert told["delete_after"] == ViewConstants.ACTION_NOTICE_SECONDS
    assert told["ephemeral"] is True
    await scenario.finish()


async def test_a_message_without_a_typed_mention_is_saved_quietly(
    scenario_factory, deps
):
    scenario = await _twitch_card(scenario_factory, deps)

    await _type_messages(scenario, "{streamer} ao vivo! {stream_link}")

    assert scenario.card_state["notification_messages"] == (
        "{streamer} ao vivo! {stream_link}"
    )
    assert _told(scenario) == []
    await scenario.finish()


async def test_a_birthday_message_saves_without_the_mentions_typed_in_it(
    scenario_factory, deps
):
    scenario = await birthdays._open(scenario_factory, LOCALE)
    await scenario.confirm()

    await scenario.click(DEFAULT_MESSAGE)
    title, content = scenario.pending_modal_fields()
    await scenario.submit_modal(
        {title: "@here Parabéns, {user}!", content: "Feliz dia! <@&201> @everyone"}
    )

    state = scenario.card_state
    assert state["default_message_title"] == "Parabéns, {user}!"
    assert state["default_message_content"] == "Feliz dia!"
    assert len(_told(scenario)) == 1, "one note for the whole modal"
    await scenario.finish()


def _message_normalizers(step):
    for section in step.sections:
        if section.key in ("messages", "default_message", "message"):
            yield section.key, [rule.name if rule else None for rule in section.normalizers()]


@pytest.mark.parametrize(
    "form",
    ["notifications_twitch", "notifications_youtube_video", "reminders_birthday"],
)
def test_every_message_a_mention_field_covers_declares_the_normalizer(form):
    cards = []
    for step in registry.get(form).steps:
        cards += [step] if isinstance(step, CardStep) else []
        if isinstance(step, ListStep):
            cards += [child for child in step.steps if isinstance(child, CardStep)]

    found = [found for card in cards for found in _message_normalizers(card)]

    assert found, "the form writes a message"
    for key, rules in found:
        assert rules and set(rules) == {"without_group_mentions"}, key

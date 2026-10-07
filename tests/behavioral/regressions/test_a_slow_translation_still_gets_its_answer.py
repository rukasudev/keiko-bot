"""A translation Google takes its time on still reaches the person who asked for it.

Reported in the v1 review of the loop and runtime PR: the message context menu answered
only once Google had, and Discord drops an interaction nobody acknowledged within three
seconds. With the time limits every outside call now has, a translation may take up to
five seconds to detect the language and then the read of the translation itself, so a
slow Google meant "The application did not respond" instead of the translation.

Shared behaviour: `Translate.translate_message`, the Translate message context menu.
Exposed by: the time limits of the shared HTTP client and of the language detection.

Guaranteed: the two checks that need no outside call answer at once; anything that
waits on Google is acknowledged first, privately and with Discord's thinking indicator,
and its answer, the translation or the error, follows up privately.
"""
from types import SimpleNamespace

import pytest

from tests.mocks.discord import MockInteraction, create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

TRANSLATED = {"src": "🇧🇷", "dest": "🇺🇸", "original": "bom dia", "translated_message": "good morning"}


@pytest.fixture
def translate(deps, guild, monkeypatch):
    from app.cogs.base.translate import Translate
    from app.integrations.google_translate import GoogleTranslate

    interaction = MockInteraction(user=create_member(guild), guild=guild)
    answered_before_google = []

    async def run(text, google_answers=None):
        def translate(content, locale):
            answered_before_google.append([dict(answer) for answer in interaction._responses])
            return google_answers

        monkeypatch.setattr(GoogleTranslate, "translate", staticmethod(translate))
        message = create_message(guild.text_channels[0], create_member(guild), text)
        message.jump_url = "https://discord.com/channels/1/2/3"
        await Translate.translate_message(SimpleNamespace(), interaction, message)

    return SimpleNamespace(run=run, interaction=interaction, before_google=answered_before_google)


@pytest.mark.parametrize("google_answers", [TRANSLATED, None], ids=["translated", "google-failed"])
async def test_a_translation_is_acknowledged_before_it_waits_on_google(translate, google_answers):
    await translate.run("bom dia", google_answers)

    [answered] = translate.before_google
    assert [answer["type"] for answer in answered] == ["defer"], "Discord hears from Keiko first"
    translate.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    final = translate.interaction.get_last_response()
    assert final["type"] == "followup_send" and final["ephemeral"] is True
    assert final["embed"] is not None


@pytest.mark.parametrize("text", ["   ", "https://keiko.gg"], ids=["empty", "only-a-link"])
async def test_a_message_with_nothing_to_translate_is_answered_at_once(translate, text):
    await translate.run(text)

    assert translate.before_google == [], "Google is never asked"
    translate.interaction.response.defer.assert_not_awaited()
    answer = translate.interaction.get_last_response()
    assert answer["type"] == "send_message" and answer["ephemeral"] is True

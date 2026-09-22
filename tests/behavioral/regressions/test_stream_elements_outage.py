"""The review says what it knows, and says when it knows nothing.

The StreamElements review counts the streamer's active commands with a
lookup. When StreamElements cannot be reached the count is absent, and the
review must say so instead of claiming zero commands.

Shared behaviour: `description-when` with a `present: false` leaf over an
answer a lookup produced (`lookup_answers`). Consumer that exposed it:
stream_elements_commands. Guaranteed: an unreachable service leaves no
number on the screen and puts the outage sentence there instead.
"""

import discord
import pytest

from tests.behavioral.harness.locators import walk_items

pytestmark = pytest.mark.behavioral

COUNTED = "Encontrei"
UNREACHABLE = "Não consegui falar com o StreamElements"


def _texts(message):
    texts = [embed.description or "" for embed in message.embeds or []]
    if message.view is not None:
        texts += [
            item.content
            for item in walk_items(message.view)
            if isinstance(item, discord.ui.TextDisplay)
        ]
    return "\n".join(texts)


async def _review_after_naming(scenario_factory, deps):
    deps.twitch.add_user("shroud", user_id="37402112")
    scenario = await scenario_factory(locale="pt-br").start("stream_elements_commands")
    await scenario.confirm()
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "shroud"})
    return scenario


async def test_an_unreachable_stream_elements_says_so_on_the_review(
    scenario_factory, deps, monkeypatch
):
    from app.settings.features import stream_elements as feature

    def unreachable(name):
        raise ConnectionError("StreamElements is down")

    monkeypatch.setattr(
        feature.StreamElementsClient, "get_channel_info", staticmethod(unreachable)
    )

    scenario = await _review_after_naming(scenario_factory, deps)

    review = _texts(scenario.current_message)
    assert UNREACHABLE in review
    assert COUNTED not in review
    await scenario.finish()


async def test_a_reachable_stream_elements_counts_the_commands_on_the_review(
    scenario_factory, deps
):
    scenario = await _review_after_naming(scenario_factory, deps)

    review = _texts(scenario.current_message)
    assert COUNTED in review and "**2**" in review
    assert UNREACHABLE not in review
    await scenario.finish()

"""The review says what it knows, and says when it knows nothing.

The StreamElements review counts the streamer's active commands with a
lookup. When StreamElements cannot be reached the count is absent, and the
review must leave the line out rather than claim a number it does not have.

Shared behaviour: a review line over an answer a lookup produced
(`lookup_answers`), which is dropped when the answer is absent. Consumer that
exposed it: stream_elements_commands. Guaranteed: an unreachable service
leaves no count on the screen, and a reachable one shows the count it found.
"""

import discord
import pytest

from tests.behavioral.harness.locators import walk_items

pytestmark = pytest.mark.behavioral

COUNTED = "Comandos ativos"


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


async def test_an_unreachable_stream_elements_leaves_no_count_on_the_review(
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
    assert COUNTED not in review
    assert "shroud" in review
    await scenario.finish()


async def test_a_reachable_stream_elements_counts_the_commands_on_the_review(
    scenario_factory, deps
):
    scenario = await _review_after_naming(scenario_factory, deps)

    review = _texts(scenario.current_message)
    assert f"{COUNTED}:** 2" in review
    await scenario.finish()

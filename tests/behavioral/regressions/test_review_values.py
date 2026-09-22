"""A value of several lines on the review is never wrapped in bold.

On the local bot (2026-09-15) the Twitch review showed "Mensagens de Notificação"
as `**` around a code block: Discord printed the asterisks. The review wrapped
every value in bold, and a list of messages formats as a code block.

Shared behaviour: the review screen of every form. Consumer that exposed it:
notifications_twitch. Guaranteed: no review text puts `**` around a block.
"""

import discord
import pytest

from tests.behavioral.harness.locators import walk_items

pytestmark = pytest.mark.behavioral


def _texts(message):
    texts = [embed.description or "" for embed in message.embeds or []]
    if message.view is not None:
        texts += [
            item.content
            for item in walk_items(message.view)
            if isinstance(item, discord.ui.TextDisplay)
        ]
    return texts


async def test_a_multi_line_value_is_never_wrapped_in_bold(scenario_factory, deps):
    deps.twitch.add_user("gaules", user_id="111")
    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await scenario.click("customize:0")
    await scenario.select_option("general")
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "gaules"})
    await scenario.click("customize:2")
    fields = scenario.pending_modal_fields()
    await scenario.submit_modal(
        {fields[0]: "{streamer} on!", fields[1]: "{stream_link}"}
    )
    await scenario.click("done")
    scenario.expect_step("confirm")

    joined = "\n".join(_texts(scenario.current_message))
    assert "gaules" in joined
    assert "**\n```" not in joined and "```**" not in joined
    await scenario.finish()

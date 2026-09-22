"""The Preview on a review shows the announcement, not "nothing yet".

Setting up a Twitch or YouTube notification ends on a review whose Preview
button reads the answers through the adapter's side-action seam. From a review
the answers hold the whole composition as one value, so the senders, which
read flat keys, found nothing and the preview answered with the empty value.

Shared behaviour: `Runtime._shown` in the adapter, which expands a card's
draft with `shown_answers` and then collapses a listed composition with
`responses_for_aside`. Consumer that exposed it: notifications_twitch.
Guaranteed: pressing Preview on a review sends the announcement of the item
the review lists, and never the empty value.

This test drives the button through the adapter on purpose. Its first version
called `responses_for_aside` directly and went on passing when the adapter
stopped calling it.
"""

import pytest

from tests.behavioral.harness import locators

pytestmark = pytest.mark.behavioral

MESSAGE = "{streamer} esta ao vivo!"


async def _review_with_one_streamer(scenario_factory, deps):
    deps.twitch.add_user("gaules", user_id="111")
    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await scenario.click("customize:0")
    await scenario.select_option("general")
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "gaules"})
    await scenario.click("customize:2")
    fields = scenario.pending_modal_fields()
    await scenario.submit_modal({fields[0]: MESSAGE})
    await scenario.click("done")
    return scenario


async def _press(scenario, message, label):
    button = locators.find_button(message, label, scenario.locale)
    interaction = scenario._mint(message=message, custom_id=button.custom_id)
    await locators.dispatch_click(message.view, button, interaction)


async def test_pressing_preview_on_the_review_announces_the_item_it_lists(
    scenario_factory, deps, monkeypatch
):
    from app.settings.features import feature_for

    seen: dict = {}

    async def spy(interaction, responses):
        seen["rows"] = {
            row["key"]: row.get("_raw_value", row.get("value"))
            for row in responses
            if row.get("key")
        }

    monkeypatch.setattr(feature_for("notifications_twitch"), "preview_sender", spy)
    scenario = await _review_with_one_streamer(scenario_factory, deps)

    await _press(scenario, scenario.current_message, "Pré-visualizar")

    assert seen["rows"].get("streamer") == "gaules", seen["rows"]
    assert seen["rows"].get("notification_messages") == MESSAGE, seen["rows"]

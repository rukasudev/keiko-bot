"""Behavioral scenario: Twitch subscribe/unsubscribe orchestration.

Runs the full notifications_twitch setup with the production is_dev gate
OPEN (config.is_dev() -> False), so `Form.pre_finish_step` executes the
real subscription orchestration against the recording MockTwitchAPI.
Only the Twitch HTTP boundary is fake; the YAML validator
(validate_streamer_name) also hits the mock for real.
"""

import pytest

from app.services.utils import ml

pytestmark = pytest.mark.behavioral

GUILD_ID = "123456789"


@pytest.fixture
def production_like_bot(deps):
    """Open the is_dev gate so pre_finish_step runs subscriptions."""
    deps.bot.config.is_dev = lambda: False
    return deps.bot


async def _card_to_review(scenario, streamer, messages=None):
    """The item card: channel, streamer and, when given, the messages; then Done."""
    await scenario.click("customize:0")
    await scenario.select_option("general")
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: streamer})
    if messages:
        await scenario.click("customize:2")
        fields = scenario.pending_modal_fields()
        await scenario.submit_modal(dict(zip(fields, messages)))
    await scenario.click("done")


async def test_the_card_shows_the_picture_of_the_streamer_just_typed(
    scenario_factory, deps
):
    """Lucas: once the streamer is typed, show their picture on the card. The
    picture comes from the same lookup that validates the nick, and it is never
    saved with the item."""
    import json

    deps.twitch.add_user("gaules", user_id="111")

    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "gaules"})

    card = json.dumps(scenario.expect_message(components_v2=True), ensure_ascii=False)
    assert "gaules.jpg" in card, "the streamer picture belongs on the card"
    await scenario.finish()


async def test_the_item_card_offers_a_preview_of_the_notification(
    scenario_factory, deps
):
    """Lucas: the same preview as the welcome message, one text at a time."""
    deps.twitch.add_user("gaules", user_id="111")

    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()

    scenario.expect_component(label_or_action="Pré-visualizar")
    await scenario.finish()


async def test_the_message_preview_walks_through_every_text():
    """The preview shows one written message per click, and wraps around."""
    from unittest.mock import AsyncMock, MagicMock

    from app.views.message_preview import MessagePreviewView

    view = MessagePreviewView(["está ao vivo!", "chegou na live", "bora?"], "pt-br")

    async def press():
        interaction = MagicMock()
        interaction.response.edit_message = AsyncMock()
        await view.next_message.callback(interaction)
        return interaction.response.edit_message.await_args.kwargs["content"]

    assert view.content == "está ao vivo!"
    assert await press() == "chegou na live"
    assert await press() == "bora?"
    assert await press() == "está ao vivo!"


async def test_full_setup_subscribes_streamer(
    scenario_factory, deps, production_like_bot
):
    deps.twitch.add_user("gaules", user_id="111")

    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await _card_to_review(scenario, "gaules")

    scenario.expect_step("confirm")
    await scenario.confirm()

    assert deps.twitch.subscribe_calls, "setup must subscribe the streamer"
    subscribed_user_ids = [call.get("user_id") for call in deps.twitch.subscribe_calls]
    assert "111" in subscribed_user_ids

    document = scenario.expect_persisted(
        "guild", "notifications_twitch", {"guild_id": GUILD_ID}, {"enabled": True}
    )
    streamers = [i["streamer"]["value"] for i in document["notifications"]["values"]]
    assert streamers == ["gaules"]
    await scenario.finish()


async def test_unknown_streamer_is_rejected_by_real_validator(
    scenario_factory, deps, production_like_bot
):
    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await scenario.click("customize:1")

    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "naoexiste"})

    error_message = ml("errors.streamer-not-found.message", locale="pt-br")
    scenario.expect_error(error_message.split(".")[0])
    assert not deps.twitch.subscribe_calls
    await scenario.finish()


async def test_disable_unsubscribes_streamers(
    scenario_factory, deps, production_like_bot
):
    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.subscribe_to_stream_online_event("111")
    cog = {
        "guild_id": GUILD_ID,
        "enabled": True,
        "notifications": {
            "style": "composition",
            "values": [
                {
                    "channel": {"value": "100", "style": "channel"},
                    "streamer": {"value": "gaules"},
                }
            ],
        },
    }
    deps.mongo_client.guild["notifications_twitch"].insert_one(dict(cog))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "notifications_twitch", cog
    )

    await scenario.click(ml("buttons.disable.label", locale="pt-br"))
    await scenario.submit_confirmation()

    assert deps.twitch.unsubscribe_calls, "disable must unsubscribe streamers"
    scenario.expect_not_persisted(
        "guild", "notifications_twitch", {"guild_id": GUILD_ID}
    )


async def test_a_streamer_typed_with_an_at_sign_is_found_and_saved_without_it(
    scenario_factory, deps, production_like_bot
):
    """The placeholder reads "your nick (e.g. @shroud)", so admins type the @:
    the lookup, the subscription and the saved item all use the bare nick."""
    deps.twitch.add_user("gaules", user_id="111")

    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await _card_to_review(scenario, " @Gaules ")
    scenario.expect_step("confirm")
    await scenario.confirm()

    assert "111" in [call.get("user_id") for call in deps.twitch.subscribe_calls]
    document = scenario.expect_persisted(
        "guild", "notifications_twitch", {"guild_id": GUILD_ID}, {"enabled": True}
    )
    streamers = [i["streamer"]["value"] for i in document["notifications"]["values"]]
    assert streamers == ["gaules"]
    await scenario.finish()


async def test_an_item_card_saves_the_item_the_notifier_reads(
    scenario_factory, deps, production_like_bot
):
    """The notifier reads channel.value, streamer.value and a ;-joined
    notification_messages.value it splits: the item card must save exactly that."""
    deps.twitch.add_user("gaules", user_id="111")

    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await _card_to_review(
        scenario,
        "gaules",
        messages=["{streamer} on! {stream_link}", "Corre, {streamer}!"],
    )
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", "notifications_twitch", {"guild_id": GUILD_ID}, {"enabled": True}
    )
    [item] = document["notifications"]["values"]
    assert item["streamer"]["value"] == "gaules"
    assert str(item["channel"]["value"]).isdigit()
    assert item["notification_messages"]["value"] == (
        "{streamer} on! {stream_link};Corre, {streamer}!"
    )
    assert "notification" not in item
    await scenario.finish()


async def test_editing_an_item_from_its_own_edit_moves_the_subscription(
    scenario_factory, deps, production_like_bot
):
    from tests.behavioral.golden.paths.common import seed_document
    from tests.behavioral.golden.paths.notifications_twitch import (
        ENABLED,
        _known_streamers,
    )

    _known_streamers(deps)
    deps.twitch.subscribe_to_stream_online_event("181077473")
    seed_document(deps, "notifications_twitch", ENABLED)
    scenario = await scenario_factory(locale="pt-br").start_command(
        "notifications_twitch"
    )

    await scenario.click("section:notifications$0")
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "shroud"})
    await scenario.click("done")

    assert "37402112" in [call.get("user_id") for call in deps.twitch.subscribe_calls]
    assert deps.twitch.unsubscribe_calls, "the old streamer is unsubscribed"
    await scenario.finish()

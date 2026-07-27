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


async def test_full_setup_subscribes_streamer(scenario_factory, deps,
                                              production_like_bot):
    deps.twitch.add_user("gaules", user_id="111")

    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()

    await scenario.select_option("general")           # composition: channel
    await scenario.confirm()
    await scenario.submit_modal(                      # streamer name; the real
        {scenario.pending_modal_fields()[0]: "gaules"}    # validator hits the mock
    )
    await scenario.confirm()                          # info/button step
    fields = {label: "@everyone {streamer} on! {stream_link}"
              for label in scenario.pending_modal_fields()}
    await scenario.submit_modal(fields)

    scenario.expect_step("confirm")
    await scenario.confirm()                          # _finish -> pre_finish_step

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
        scenario_factory, deps, production_like_bot):
    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await scenario.select_option("general")
    await scenario.confirm()

    await scenario.submit_modal(
        {scenario.pending_modal_fields()[0]: "naoexiste"}
    )

    error_message = ml("errors.streamer-not-found.message", locale="pt-br")
    scenario.expect_error(error_message.split(".")[0])
    assert not deps.twitch.subscribe_calls
    await scenario.finish()


async def test_disable_unsubscribes_streamers(scenario_factory, deps,
                                              production_like_bot):
    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.subscribe_to_stream_online_event("111")
    cog = {
        "guild_id": GUILD_ID, "enabled": True,
        "notifications": {
            "style": "composition",
            "values": [{"channel": {"value": "100", "style": "channel"},
                        "streamer": {"value": "gaules"}}],
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

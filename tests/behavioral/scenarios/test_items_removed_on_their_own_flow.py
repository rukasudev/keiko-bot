"""Behavioral scenario: a streamer or a channel removed on its own.

N5 of the beta.2 round 2 put a Remove beside each item of the Twitch and
YouTube panels; removing the only item turns the feature off. Round 3 (N1)
took the per-item pause off: an item a dev panel saved with `enabled: false`
loads, notifies and edits like any other.
"""

import pytest

from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths.common import GUILD_ID, seed_document
from app.data.guild.cogs import find_cog_by_guild_id

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GAULES_ID = "181077473"
SHROUD_ID = "37402112"


@pytest.fixture(autouse=True)
def outside_dev(deps):
    """The subscription hooks run only outside dev."""
    deps.bot.config.is_dev = lambda: False


def _stored(deps, form, guild_id=GUILD_ID):
    return find_cog_by_guild_id(guild_id, form)


def _items(deps, form=twitch.FORM):
    return _stored(deps, form)["notifications"]["values"]


async def _twitch_panel(scenario_factory, deps, document=twitch.ENABLED):
    twitch._known_streamers(deps)
    deps.twitch.subscribe_to_stream_online_event(GAULES_ID)
    seed_document(deps, twitch.FORM, document)
    return await scenario_factory(locale="pt-br").start_command(twitch.FORM)


async def test_a_streamer_saved_by_the_old_pause_is_edited_like_any_other(
    scenario_factory, deps
):
    paused = {**twitch.notification("100", "gaules"), "enabled": False}
    document = {
        **twitch.ENABLED,
        "notifications": {
            "style": "composition",
            "values": [paused, twitch.notification("102", "cellbit")],
        },
    }
    scenario = await _twitch_panel(scenario_factory, deps, document)

    await scenario.click("section:notifications$0")
    await twitch._submit_streamer(scenario, "shroud")
    await scenario.click("done")

    assert _items(deps)[0]["streamer"]["value"] == "shroud"
    assert "enabled" not in _items(deps)[0], "the old field is not carried over"
    subscribed = [call.get("user_id") for call in deps.twitch.subscribe_calls]
    assert SHROUD_ID in subscribed, "the new streamer is subscribed as usual"
    await scenario.finish()


async def test_removing_the_only_streamer_asks_and_turns_twitch_off(
    scenario_factory, deps
):
    only = {
        **twitch.ENABLED,
        "notifications": {
            "style": "composition",
            "values": [twitch.notification("100", "gaules")],
        },
    }
    scenario = await _twitch_panel(scenario_factory, deps, only)

    await scenario.click("remove_this:notifications$0")
    scenario.expect_message(title_contains="Ei, espere! Leia abaixo primeiro")
    [warning] = scenario.current_message.embeds
    assert warning.description.startswith("Você só tem **1** streamer configurado.")
    assert "### Notificações #1" in warning.description
    assert "**Streamer:** gaules" in warning.description
    assert "a função **Twitch Lives** será **desativada**" in warning.description
    await scenario.click("remove_items")

    assert _stored(deps, twitch.FORM) is None
    assert deps.twitch.unsubscribe_calls, "the feature off lets gaules go"
    scenario.expect_message(title_contains="desativado")


async def test_removing_the_only_streamer_after_another_panel_added_one_drops_nothing(
    scenario_factory, deps
):
    only = {
        **twitch.ENABLED,
        "notifications": {
            "style": "composition",
            "values": [twitch.notification("100", "gaules")],
        },
    }
    scenario = await _twitch_panel(scenario_factory, deps, only)
    await scenario.click("remove_this:notifications$0")
    deps.mongo_client.guild.cogs.update_one(
        {"_id": f"{GUILD_ID}:{twitch.FORM}"},
        {"$set": {"options.notifications.values": [
            twitch.notification("100", "gaules"),
            twitch.notification("102", "cellbit"),
        ]}},
    )

    await scenario.click("remove_items")

    assert [item["streamer"]["value"] for item in _items(deps)] == ["gaules", "cellbit"]
    assert deps.twitch.unsubscribe_calls == []
    await scenario.finish()

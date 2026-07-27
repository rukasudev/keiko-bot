"""Behavioral scenarios: Manager lifecycle through the real buttons.

Pause, unpause and disable go through the real ConfirmationModal (the user
must type the action word). Add/remove item run the real composition
sub-form and the real Select-based remove view.
"""
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

BLOCK_LINKS_COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_links": ["Youtube"],
    "answer": "Nada de links!",
}


def _twitch_cog(*streamers: str) -> dict:
    return {
        "guild_id": GUILD_ID, "enabled": True,
        "notifications": {
            "style": "composition",
            "values": [
                {"channel": {"value": "100", "style": "channel"},
                 "streamer": {"value": name},
                 "notification_messages": {"value": f"{name} on!"}}
                for name in streamers
            ],
        },
    }


async def test_pause_requires_typed_confirmation_and_pauses(scenario_factory, deps):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS_COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(BLOCK_LINKS_COG)
    )

    pause_label = ml("buttons.pause.label", locale="pt-br")
    await scenario.click(pause_label)
    scenario.expect_modal()                      # ConfirmationModal pending

    await scenario.submit_confirmation()         # types the action word
    scenario.expect_message(
        title_contains=ml("commands.command-events.paused.title", locale="pt-br"),
    )
    scenario.expect_persisted(
        "guild", "moderations", {"guild_id": GUILD_ID}, {"block_links": False}
    )
    scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"enabled": False}
    )


async def test_wrong_confirmation_word_does_not_pause(scenario_factory, deps):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS_COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(BLOCK_LINKS_COG)
    )
    await scenario.click(ml("buttons.pause.label", locale="pt-br"))
    await scenario.submit_confirmation(word="nope")

    document = scenario.get_persisted("guild", "block_links", {"guild_id": GUILD_ID})
    assert document["enabled"] is True, "wrong confirmation word must be a no-op"


async def test_unpause_reenables_command(scenario_factory, deps):
    paused = {**BLOCK_LINKS_COG, "enabled": False}
    deps.mongo_client.guild["block_links"].insert_one(dict(paused))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(paused)
    )

    await scenario.click(ml("buttons.unpause.label", locale="pt-br"))
    await scenario.submit_confirmation()

    scenario.expect_message(
        title_contains=ml("commands.command-events.unpaused.title", locale="pt-br"),
    )
    scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"enabled": True}
    )
    scenario.expect_persisted(
        "guild", "moderations", {"guild_id": GUILD_ID}, {"block_links": True}
    )


async def test_disable_deletes_configuration(scenario_factory, deps):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS_COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(BLOCK_LINKS_COG)
    )

    await scenario.click(ml("buttons.disable.label", locale="pt-br"))
    await scenario.submit_confirmation()

    scenario.expect_message(
        title_contains=ml("commands.command-events.disabled.title", locale="pt-br"),
    )
    scenario.expect_not_persisted("guild", "block_links", {"guild_id": GUILD_ID})


async def test_add_item_runs_real_subform_and_appends(scenario_factory, deps):
    deps.twitch.add_user("cellbit", user_id="222")
    cog = _twitch_cog("gaules")
    deps.mongo_client.guild["notifications_twitch"].insert_one(dict(cog))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "notifications_twitch", cog
    )

    await scenario.click(ml("buttons.add.label", locale="pt-br"))
    await scenario.select_option("general")          # composition: channel
    await scenario.confirm()
    await scenario.submit_modal(                      # streamer (real validator
        {scenario.pending_modal_fields()[0]: "cellbit"}   # -> MockTwitchAPI)
    )
    await scenario.confirm()                          # button info step
    fields = {label: "@everyone {streamer} on! {stream_link}"
              for label in scenario.pending_modal_fields()}
    await scenario.submit_modal(fields)               # notification messages

    scenario.expect_message(title_contains="Item adicionado")
    streamers = [
        item["streamer"]["value"]
        for item in cog["notifications"]["values"]
    ]
    assert streamers == ["gaules", "cellbit"]
    document = scenario.get_persisted(
        "guild", "notifications_twitch", {"guild_id": GUILD_ID}
    )
    persisted = [i["streamer"]["value"] for i in document["notifications"]["values"]]
    assert persisted == ["gaules", "cellbit"]
    await scenario.finish()


async def test_remove_item_unsubscribes_and_updates_document(scenario_factory, deps):
    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.add_user("cellbit", user_id="222")
    deps.twitch.subscribe_to_stream_online_event("111")
    cog = _twitch_cog("gaules", "cellbit")
    deps.mongo_client.guild["notifications_twitch"].insert_one(dict(cog))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "notifications_twitch", cog
    )

    await scenario.click(ml("buttons.remove.label", locale="pt-br"))
    await scenario.select_option("notifications$0")   # remove "gaules"

    scenario.expect_message(title_contains="Item removido")
    document = scenario.get_persisted(
        "guild", "notifications_twitch", {"guild_id": GUILD_ID}
    )
    persisted = [i["streamer"]["value"] for i in document["notifications"]["values"]]
    assert persisted == ["cellbit"]
    assert deps.twitch.unsubscribe_calls, "removing the last guild must unsubscribe"

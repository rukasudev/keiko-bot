"""Behavioral scenarios: Manager lifecycle through the real buttons.

Pause and Despausar save in one click, draw the same panel again with the
feature's new state and tell the change on a message that removes itself.
Desativar asks first, on a red screen that says what goes, with Desativar e
apagar and Voltar. Add/remove item run the real composition sub-form and the
real Select-based remove view.
"""

import pytest

from app.constants import ViewConstants
from app.core.utils import ml
from tests.generators.moderations import seed_cog

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"
LOCALE = "pt-br"

BLOCK_LINKS_COG = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_links": ["Youtube"],
    "answer": "Nada de links!",
}


def _twitch_cog(*streamers: str) -> dict:
    return {
        "guild_id": GUILD_ID,
        "enabled": True,
        "notifications": {
            "style": "composition",
            "values": [
                {
                    "channel": {"value": "100", "style": "channel"},
                    "streamer": {"value": name},
                    "notification_messages": {"value": f"{name} on!"},
                }
                for name in streamers
            ],
        },
    }


async def _panel(scenario_factory, deps, cog):
    seed_cog(deps.mongo_client, "block_links", dict(cog))
    return await scenario_factory(locale=LOCALE).start_manager("block_links", dict(cog))


def _last_screen(scenario) -> str:
    """The last screen drawn, a message that removes itself left out."""
    return str(next(
        event for event in reversed(scenario.outputs)
        if event.get("actor") == "bot" and not event.get("delete_after")
        and (event.get("components") or event.get("embed"))
    ))


def _told(scenario, kind: str) -> None:
    """The change's own message came last, and removes itself after a while."""
    told = scenario.outputs[-1]
    assert told["embed"]["title"] == ml(f"commands.responses.{kind}.title", locale=LOCALE)
    assert "**moderações bloquear links**" in told["embed"]["description"]
    assert told["delete_after"] == ViewConstants.ACTION_NOTICE_SECONDS


def _enabled(scenario) -> bool:
    document = scenario.get_persisted("guild", "block_links", {"guild_id": GUILD_ID})
    return document["enabled"]


async def test_pause_saves_in_one_click_and_draws_the_panel_paused(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps, BLOCK_LINKS_COG)
    panel = scenario.current_message

    await scenario.click(ml("buttons.pause.label", locale=LOCALE))

    assert scenario.current_message is panel, "the panel stays on its message"
    assert f"({ml('texts.paused_suffix', locale=LOCALE)})" in _last_screen(scenario)
    scenario.expect_component(label_or_action="unpause")
    assert ml("buttons.unpause.label", locale=LOCALE) == "Despausar"
    _told(scenario, "paused")
    assert _enabled(scenario) is False


async def test_resume_saves_in_one_click_and_draws_the_panel_running(
    scenario_factory, deps
):
    scenario = await _panel(scenario_factory, deps, {**BLOCK_LINKS_COG, "enabled": False})
    panel = scenario.current_message

    await scenario.click(ml("buttons.unpause.label", locale=LOCALE))

    assert scenario.current_message is panel
    assert f"({ml('texts.paused_suffix', locale=LOCALE)})" not in _last_screen(scenario)
    scenario.expect_component(label_or_action="pause")
    _told(scenario, "unpaused")
    assert _enabled(scenario) is True


async def test_delete_asks_first_and_back_keeps_everything(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps, BLOCK_LINKS_COG)

    await scenario.click(ml("buttons.disable.label", locale=LOCALE))

    question = _last_screen(scenario)
    assert ml("commands.responses.delete.title", locale=LOCALE) in question
    assert "**moderações bloquear links**" in question, "it names the command"
    assert ml("buttons.pause.label", locale=LOCALE) in question, "it offers a pause"
    scenario.expect_component(label_or_action="delete_all")

    await scenario.go_back()

    scenario.expect_component(label_or_action="pause")
    assert _enabled(scenario) is True, "Voltar deletes nothing"


async def test_a_paused_feature_is_asked_without_the_pause_hint(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps, {**BLOCK_LINKS_COG, "enabled": False})

    await scenario.click(ml("buttons.disable.label", locale=LOCALE))

    question = _last_screen(scenario)
    assert "**moderações bloquear links**" in question
    assert ml("buttons.pause.label", locale=LOCALE) not in question


async def test_delete_everything_deletes_the_configuration(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps, BLOCK_LINKS_COG)

    await scenario.click(ml("buttons.disable.label", locale=LOCALE))
    await scenario.click("delete_all")

    scenario.expect_message(
        title_contains=ml("commands.responses.disabled.title", locale=LOCALE),
    )
    scenario.expect_not_persisted("guild", "block_links", {"guild_id": GUILD_ID})


async def test_add_item_runs_real_subform_and_appends(scenario_factory, deps):
    deps.twitch.add_user("cellbit", user_id="222")
    cog = _twitch_cog("gaules")
    seed_cog(deps.mongo_client, "notifications_twitch", dict(cog))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "notifications_twitch", cog
    )

    await scenario.click(ml("buttons.add.label", locale="pt-br"))
    await scenario.select_option("general", target="channel")
    await scenario.click("customize:1")  # streamer (real validator
    await scenario.submit_modal(  # -> MockTwitchAPI)
        {scenario.pending_modal_fields()[0]: "cellbit"}
    )
    await scenario.click("customize:4")  # notification messages
    first = scenario.pending_modal_fields()[0]
    await scenario.submit_modal({first: "@everyone {streamer} on! {stream_link}"})
    await scenario.click("done")

    scenario.expect_component(label_or_action="pause")
    assert "cellbit" in str(scenario.outputs[-1]), "the panel lists the new streamer"
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
    seed_cog(deps.mongo_client, "notifications_twitch", dict(cog))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "notifications_twitch", cog
    )

    await scenario.click("remove_this:notifications$0")
    await scenario.click("remove_items")

    scenario.expect_component(label_or_action="pause")
    panel = str(next(event for event in reversed(scenario.outputs) if event.get("components_v2")))
    assert "cellbit" in panel and "gaules" not in panel, "the panel lists what is left"
    document = scenario.get_persisted(
        "guild", "notifications_twitch", {"guild_id": GUILD_ID}
    )
    persisted = [i["streamer"]["value"] for i in document["notifications"]["values"]]
    assert persisted == ["cellbit"]
    assert deps.twitch.unsubscribe_calls, "removing the last guild must unsubscribe"


async def test_the_pause_message_is_dated_by_the_click(scenario_factory, deps):
    """The panel's message can be hours old when Pausar is pressed: the message tells
    the moment of the click, never the panel's last edit."""
    from datetime import datetime, timezone

    from app.core.utils import discord_time
    from tests.behavioral.harness import locators

    scenario = await _panel(scenario_factory, deps, BLOCK_LINKS_COG)
    panel = scenario.current_message
    panel.edited_at = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    pause = locators.find_button(
        panel, ml("buttons.pause.label", locale=LOCALE), scenario.locale
    )
    click = scenario._mint(message=panel, custom_id=pause.custom_id)
    click.created_at = datetime(2026, 1, 1, 12, 34, tzinfo=timezone.utc)

    await locators.dispatch_click(panel.view, pause, click)

    told = scenario.outputs[-1]["embed"]["description"]
    assert discord_time(click.created_at) in told
    assert discord_time(panel.edited_at) not in told

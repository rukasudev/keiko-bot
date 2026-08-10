"""Regression: a command-event message states the event and nothing else.

What broke, twice, for the same underlying reason — the announcement is built
from what the panel left behind:

1. The manager reused the panel embed to announce pause, unpause, disable and
   edit, rewriting title and description on the same object. While every design
   wrote settings only into `description`, overwriting it was enough to clean
   it; the moment a design rendered each setting as an embed field, the
   "command paused" announcement carried the whole configuration under it.
2. The buttons under the announcement came from the Manager, whose `cogs`
   snapshot was taken *before* the change. Pause, unpause and disable always
   cleared them; edit, add and remove did not, so the announcement offered
   actions on data that no longer existed. The Components V2 panel is what
   exposed it: the clicked button's `self.view` is the container, so clearing
   it left the Manager — the view the announcement is sent with — untouched.

Shared behavior affected: `app/views/manager.py` (`_event_embed`,
`_announce_event`, every lifecycle callback) and `app/views/form.py::_finish`,
consumed by every command that has a manager.

Must remain guaranteed: an event message (paused, unpaused, disabled, edited,
item added, item removed) shows the event only — no embed fields, none of the
saved configuration, and no manager controls.
"""
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.regression,
              pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

BLOCK_LINKS_COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_links": ["Youtube"],
    "answer": "Nada de links!",
    "custom_links": {
        "style": "composition",
        "values": [
            {"link": {"value": "twitch.tv/jway"},
             "match_type": {"value": "domain"}},
        ],
    },
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


def _event(scenario) -> dict:
    return scenario.outputs[-1]


def _event_fields(scenario) -> list:
    embed = _event(scenario).get("embed") or {}
    return embed.get("fields") or []


def _event_text(scenario) -> str:
    embed = _event(scenario).get("embed") or {}
    return " ".join(filter(None, [
        _event(scenario).get("content"), embed.get("title"), embed.get("description"),
    ]))


def _event_buttons(scenario) -> list:
    def walk(items):
        for item in items:
            if item.get("type") == "button":
                yield item.get("label")
            yield from walk(item.get("children") or [])

    return list(walk(_event(scenario).get("components") or []))


def _assert_states_only_the_event(scenario, event: str, *configured: str) -> None:
    assert _event_fields(scenario) == [], (
        f"the {event} message kept settings fields: {_event_fields(scenario)}"
    )
    assert _event_buttons(scenario) == [], (
        f"the {event} message kept the manager controls: {_event_buttons(scenario)}"
    )
    leaked = [value for value in configured if value in _event_text(scenario)]
    assert not leaked, (
        f"the {event} message repeats the configuration {leaked}: "
        f"{_event_text(scenario)}"
    )


async def _manager(scenario_factory, deps, cog=None):
    from app.services.block_links import normalize_block_links_config

    cog = cog or dict(BLOCK_LINKS_COG)
    deps.mongo_client.guild["block_links"].insert_one(dict(cog))
    return await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(cog)
    )


@pytest.mark.parametrize(
    "button_key, title_key",
    [
        ("pause", "paused"),
        ("disable", "disabled"),
    ],
)
async def test_lifecycle_event_states_only_the_event(
    scenario_factory, deps, button_key, title_key
):
    scenario = await _manager(scenario_factory, deps)
    assert "Nada de links!" in scenario.rendered_summary, \
        "the panel starts showing the configuration"

    await scenario.click(ml(f"buttons.{button_key}.label", locale="pt-br"))
    await scenario.submit_confirmation()

    scenario.expect_message(
        title_contains=ml(f"commands.command-events.{title_key}.title", locale="pt-br"),
    )
    _assert_states_only_the_event(scenario, title_key, "Nada de links!", "<#100>")


async def test_unpause_event_states_only_the_event(scenario_factory, deps):
    paused = {**BLOCK_LINKS_COG, "enabled": False}
    scenario = await _manager(scenario_factory, deps, cog=paused)

    await scenario.click(ml("buttons.unpause.label", locale="pt-br"))
    await scenario.submit_confirmation()

    scenario.expect_message(
        title_contains=ml("commands.command-events.unpaused.title", locale="pt-br"),
    )
    _assert_states_only_the_event(scenario, "unpaused", "Nada de links!", "<#100>")


async def test_edit_event_states_only_the_event(scenario_factory, deps):
    """The edit flow ends in `Form._finish`, which reuses the panel's embed.
    On the panel the section's own button jumps straight into its step."""
    scenario = await _manager(scenario_factory, deps)

    await scenario.click("section:link_settings")     # the section's own pencil
    await scenario.click("customize:2")               # answer modal-input
    await scenario.submit_modal({"resposta": "Sem links aqui!"})
    await scenario.click("done")

    scenario.expect_message(
        title_contains=ml("commands.command-events.edited.title", locale="pt-br"),
    )
    _assert_states_only_the_event(scenario, "edited", "Sem links aqui!", "<#100>")


async def test_add_item_event_states_only_the_event(scenario_factory, deps):
    deps.twitch.add_user("cellbit", user_id="222")
    cog = _twitch_cog("gaules")
    deps.mongo_client.guild["notifications_twitch"].insert_one(dict(cog))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "notifications_twitch", cog
    )

    await scenario.click(ml("buttons.add.label", locale="pt-br"))
    await scenario.select_option("general")
    await scenario.confirm()
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "cellbit"})
    await scenario.confirm()
    await scenario.submit_modal({
        label: "@everyone {streamer} on! {stream_link}"
        for label in scenario.pending_modal_fields()
    })

    scenario.expect_message(title_contains="Item adicionado")
    _assert_states_only_the_event(scenario, "added", "gaules", "<#100>")
    await scenario.finish()


async def test_remove_item_event_states_only_the_event(scenario_factory, deps):
    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.add_user("cellbit", user_id="222")
    cog = _twitch_cog("gaules", "cellbit")
    deps.mongo_client.guild["notifications_twitch"].insert_one(dict(cog))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "notifications_twitch", cog
    )

    await scenario.click(ml("buttons.remove.label", locale="pt-br"))
    await scenario.select_option("notifications$0")

    scenario.expect_message(title_contains="Item removido")
    _assert_states_only_the_event(scenario, "removed", "cellbit", "<#100>")

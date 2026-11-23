"""Behavioral scenarios: what an admin does on a feature's panel, and where it ends.

Every change saved from the panel (Adicionar, Editar of an item or of a card,
Remover, Pausar, Despausar) ends on the panel drawn again over what is saved now,
on the message the admin was using, instead of a closed embed with a final message,
and records the same product events and story as when the message closed. Pause and
Despausar save in one click and tell the change on a message that removes itself,
dated by the click. Desativar asks first, on a red screen that says what goes, with
Desativar e apagar and Voltar. An edit opens the card prefilled for that step only,
hides a step whose condition the saved document fails, and merges the change into
what is saved, a legacy document included.

The panel a setup sends below its activation embed is a panel like any other (the
contract over every form is `tests/behavioral/contracts/
test_a_setup_ends_with_the_activation_and_its_panel.py`): left untouched it is no
abandoned setup, a change saved from it tells a story of its own, and when the
feature cannot be read again only the activation embed stays.
"""
import pytest

from app.constants import ViewConstants
from app.core.commands import command_for
from app.core.utils import ml
from app.data import data
from app.forms.features import feature_for
from tests.behavioral.flows import twitch
from tests.behavioral.flows.birthdays import (
    birthday_guild,
    complete_member_card,
    complete_settings_card,
    seed_config,
)
from tests.behavioral.flows.common import GUILD_ID, open_manager, seed_document
from tests.behavioral.harness.copy_keys import copy_text
from tests.generators.moderations import seed_cog
from tests.helpers.slash_tree import synced_tree

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

LOCALE = "pt-br"
BIRTHDAYS = "reminders_birthday"
CLOSED_TITLES = [
    ml(f"commands.responses.{kind}.title", locale=LOCALE)
    for kind in ("enabled", "disabled")
]

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


def _drawn(scenario):
    """Every screen drawn as Components V2, oldest first."""
    return [
        event
        for event in scenario.outputs
        if event.get("actor") == "bot" and event.get("components_v2")
    ]


def _last_screen(scenario) -> str:
    """The last screen drawn, a message that removes itself left out."""
    return str(next(
        event for event in reversed(scenario.outputs)
        if event.get("actor") == "bot" and not event.get("delete_after")
        and (event.get("components") or event.get("embed"))
    ))


def _closed_embeds(scenario):
    return [
        event
        for event in scenario.outputs
        if any(
            title.split("**")[0] in ((event.get("embed") or {}).get("title") or "")
            for title in CLOSED_TITLES
        )
    ]


def _names(recorded):
    return [event["type"] for event in recorded]


async def _block_links_command():
    """The command a lifecycle message names, as a member types it, in bold."""
    [path] = (await synced_tree()).path_of(command_for("block_links").cogs, LOCALE)
    return f"**{path.removeprefix('/')}**"


async def _told(scenario, kind: str) -> None:
    """The change's own message came last, and removes itself after a while."""
    told = scenario.outputs[-1]
    assert told["embed"]["title"] == ml(f"commands.responses.{kind}.title", locale=LOCALE)
    assert await _block_links_command() in told["embed"]["description"]
    assert told["delete_after"] == ViewConstants.ACTION_NOTICE_SECONDS


def _enabled(scenario) -> bool:
    document = scenario.get_persisted("guild", "block_links", {"guild_id": GUILD_ID})
    return document["enabled"]


async def _birthdays_set_up(scenario_factory, guild):
    scenario = await scenario_factory(locale=LOCALE, guild=guild).start_command(BIRTHDAYS)
    await scenario.confirm()
    await complete_settings_card(scenario)
    await scenario.confirm()
    return scenario


# ------------------------------------------------------------------ lifecycle


async def test_pause_saves_in_one_click_and_draws_the_panel_paused(
    scenario_factory, deps, analytics_events
):
    scenario = await _panel(scenario_factory, deps, BLOCK_LINKS_COG)
    panel = scenario.current_message

    await scenario.click("pause")

    assert scenario.current_message is panel, "the panel stays on its message"
    assert f"({ml('texts.paused_suffix', locale=LOCALE)})" in _last_screen(scenario)
    assert _closed_embeds(scenario) == []
    scenario.expect_component(label_or_action="unpause")
    await _told(scenario, "paused")
    assert _enabled(scenario) is False
    assert "feature.paused" in _names(analytics_events)


async def test_resume_saves_in_one_click_and_draws_the_panel_running(
    scenario_factory, deps, analytics_events
):
    scenario = await _panel(scenario_factory, deps, {**BLOCK_LINKS_COG, "enabled": False})
    panel = scenario.current_message

    await scenario.click("unpause")

    assert scenario.current_message is panel
    assert f"({ml('texts.paused_suffix', locale=LOCALE)})" not in _last_screen(scenario)
    assert _closed_embeds(scenario) == []
    scenario.expect_component(label_or_action="pause")
    await _told(scenario, "unpaused")
    assert _enabled(scenario) is True
    assert "feature.unpaused" in _names(analytics_events)


async def test_the_pause_message_is_dated_by_the_click(scenario_factory, deps):
    """The panel's message can be hours old when Pausar is pressed: the message tells
    the moment of the click, never the panel's last edit."""
    from datetime import datetime, timezone

    from app.core.utils import discord_time

    scenario = await _panel(scenario_factory, deps, BLOCK_LINKS_COG)
    panel = scenario.current_message
    panel.edited_at = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    clicked_at = datetime(2026, 1, 1, 12, 34, tzinfo=timezone.utc)

    await scenario.click("pause", at=clicked_at)

    told = scenario.outputs[-1]["embed"]["description"]
    assert discord_time(clicked_at) in told
    assert discord_time(panel.edited_at) not in told


async def test_delete_asks_first_and_back_keeps_everything(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps, BLOCK_LINKS_COG)

    await scenario.click("disable")

    question = _last_screen(scenario)
    assert ml("commands.responses.delete.title", locale=LOCALE) in question
    assert await _block_links_command() in question, "it names the command"
    assert ml("buttons.pause.label", locale=LOCALE) in question, "it offers a pause"
    scenario.expect_component(label_or_action="delete_all")

    await scenario.go_back()

    scenario.expect_component(label_or_action="pause")
    assert _enabled(scenario) is True, "Voltar deletes nothing"


async def test_a_paused_feature_is_asked_without_the_pause_hint(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps, {**BLOCK_LINKS_COG, "enabled": False})

    await scenario.click("disable")

    question = _last_screen(scenario)
    assert await _block_links_command() in question
    assert ml("buttons.pause.label", locale=LOCALE) not in question


async def test_delete_everything_deletes_the_configuration(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps, BLOCK_LINKS_COG)

    await scenario.click("disable")
    await scenario.click("delete_all")

    scenario.expect_message(
        title_contains=ml("commands.responses.disabled.title", locale=LOCALE),
    )
    scenario.expect_not_persisted("guild", "block_links", {"guild_id": GUILD_ID})


# --------------------------------------------------------------------- items


async def test_add_item_runs_real_subform_and_appends(scenario_factory, deps):
    deps.twitch.add_user("cellbit", user_id="222")
    cog = _twitch_cog("gaules")
    seed_cog(deps.mongo_client, "notifications_twitch", dict(cog))
    scenario = await scenario_factory(locale=LOCALE).start_manager(
        "notifications_twitch", cog
    )

    await scenario.click("add")
    await twitch.pick_channel(scenario)
    await twitch.submit_streamer(scenario, "cellbit")
    await scenario.click("customize:messages")
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


async def test_remove_item_unsubscribes_and_updates_document(
    scenario_factory, deps, analytics_events
):
    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.add_user("cellbit", user_id="222")
    deps.twitch.subscribe_to_stream_online_event("111")
    cog = _twitch_cog("gaules", "cellbit")
    seed_cog(deps.mongo_client, "notifications_twitch", dict(cog))
    scenario = await scenario_factory(locale=LOCALE).start_manager(
        "notifications_twitch", cog
    )

    await scenario.click("remove_this:notifications$0")
    await scenario.click("remove_items")

    scenario.expect_component(label_or_action="pause")
    drawn = str(_drawn(scenario)[-1])
    assert "cellbit" in drawn and "gaules" not in drawn, "the panel lists what is left"
    assert _closed_embeds(scenario) == []
    document = scenario.get_persisted(
        "guild", "notifications_twitch", {"guild_id": GUILD_ID}
    )
    persisted = [i["streamer"]["value"] for i in document["notifications"]["values"]]
    assert persisted == ["cellbit"]
    assert deps.twitch.unsubscribe_calls, "removing the last guild must unsubscribe"
    assert "feature.item_removed" in _names(analytics_events)


async def test_a_birthday_added_from_the_panel_returns_to_the_updated_panel(
    scenario_factory, deps, analytics_events
):
    await seed_config(deps)
    scenario = await scenario_factory(locale=LOCALE, guild=birthday_guild()).start_command(
        BIRTHDAYS
    )
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    card = _drawn(scenario)[-1]["message"]

    await complete_member_card(scenario, LOCALE)

    panel = _drawn(scenario)[-1]
    assert panel["message"] == card, "the panel is drawn on the card's own message"
    assert "<@777>" in str(panel), "the panel lists the member just added"
    assert _closed_embeds(scenario) == []
    scenario.expect_component(label_or_action="add")
    scenario.expect_persisted(
        "guild",
        "members",
        {
            "guild_id": str(scenario.guild.id),
            "user_id": "777",
            "options.reminders_birthday.date": "05-12",
        },
        {},
    )
    assert "feature.item_added" in _names(analytics_events)


@pytest.mark.usefixtures("outside_dev")
async def test_a_streamer_edited_from_the_panel_returns_to_the_updated_panel(
    scenario_factory, deps, analytics_events
):
    scenario = await open_manager(scenario_factory, deps, LOCALE, twitch.FORM, twitch.seed)
    await scenario.click("section:notifications$0")
    await twitch.pick_channel(scenario, "announcements")
    await scenario.click("done")

    panel = str(_drawn(scenario)[-1])
    assert "<#102>" in panel and "gaules" in panel, "the panel shows the edited item"
    assert _closed_embeds(scenario) == []
    scenario.expect_component(label_or_action="pause")
    assert "config.changed" in _names(analytics_events)


# ---------------------------------------------------------------------- edits


async def test_edit_card_updates_answer_and_upgrades_legacy_shape(
    scenario_factory, deps
):
    """A legacy (pre-redesign) document is edited through the new card: the
    card hydrates from the translated config, only the edited field changes
    meaningfully, and untouched keys survive, the quick picks upgraded to domains
    and the exempt channels, now a part of the card, written back as a list."""
    from app.commands.block_links.service import normalize_block_links_config

    legacy = {
        "guild_id": GUILD_ID,
        "enabled": True,
        "allowed_chats": {"style": "channel", "values": "100"},
        "allowed_links": ["Youtube"],
        "answer": "Resposta antiga",
    }
    seed_cog(deps.mongo_client, "block_links", dict(legacy))
    scenario = await scenario_factory(locale=LOCALE).start_manager(
        "block_links", normalize_block_links_config(legacy)
    )

    await scenario.click("section:link_settings")

    scenario.expect_message(components_v2=True)
    assert scenario.card_state["mode"] == "block_all"
    assert scenario.card_state["allowed_links"] == ["youtube.com"], (
        "legacy labels must hydrate as translated domains"
    )
    assert scenario.card_state["answer"] == "Resposta antiga"

    await scenario.click("customize:answer")
    await scenario.submit_modal({"resposta": "Resposta nova"})
    await scenario.click("done")

    scenario.expect_component(label_or_action="pause")
    assert "Resposta nova" in str(scenario.outputs[-1]), "the panel shows the edit"
    document = scenario.expect_persisted(
        "guild",
        "block_links",
        {"guild_id": GUILD_ID},
        {"answer": "Resposta nova", "mode": "block_all"},
    )
    assert document["allowed_chats"]["values"] == ["100"]
    assert document["allowed_links"]["values"] == ["youtube.com"]


def test_edit_conditional_step_is_hidden_when_condition_unmet():
    """The edit picker leaves out steps whose `when` the saved document does
    not satisfy (a "later" gate hides the list it gates)."""
    from app.forms.form.responses.summary import edit_options
    from tests.forms.form.gate_fixtures import gated_list

    options = edit_options(
        gated_list(), {"add_now": False, "things": {"values": []}}, LOCALE
    )
    option_values = [option.value for option in options]
    assert "things" not in option_values, (
        "composition step must be hidden when its gate condition is unmet"
    )


async def test_editing_one_default_roles_select_commits_it_from_its_own_edit(
    scenario_factory, deps
):
    """Each roles dropdown has its own Edit on the panel: it opens only that
    dropdown and saves as soon as a role is chosen, leaving the other alone."""
    from tests.behavioral.flows.roles import ENABLED, server_where_keiko_gives_every_role

    seed_document(deps, "default_roles", ENABLED)
    guild = server_where_keiko_gives_every_role()
    scenario = await scenario_factory(locale=LOCALE, guild=guild).start_command(
        "default_roles"
    )

    await scenario.click("section:default_roles_config/default_roles")
    await scenario.select_option("Admin", target="default_roles")

    scenario.expect_component(label_or_action="pause")
    assert "<@&200>" in str(scenario.outputs[-1]), "the panel shows the new role"
    document = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles")

    def ids(entry):
        values = entry["values"]
        return {str(v) for v in (values if isinstance(values, list) else [values])}

    assert ids(document["default_roles_bot"]) == {"201"}, (
        "the other dropdown is left alone"
    )
    assert ids(document["default_roles"]) == {"200"}, "the chosen role is saved"


async def test_a_role_keiko_cannot_give_picked_from_its_own_edit_saves_nothing(
    scenario_factory, deps
):
    """A dropdown's own Edit on the panel that only picks roles Keiko cannot give
    stays on that dropdown, saves nothing and says which roles it refused."""
    from tests.behavioral.flows.roles import ENABLED

    seed_document(deps, "default_roles", ENABLED)
    scenario = await scenario_factory(locale=LOCALE).start_command("default_roles")
    await scenario.click("section:default_roles_config/default_roles")
    shown = scenario.current_message

    await scenario.select_option("Admin", target="default_roles")

    document = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles")
    assert document["default_roles"]["values"] == "202", "nothing is saved"
    assert scenario.current_message is shown, "the dropdown stays on screen"
    scenario.expect_component(label_or_action="back")
    scenario.expect_warning(
        copy_text("commands.responses.roles_not_assignable.lines.above", LOCALE, role="200")
    )


# ------------------------------------------------------ the panel after a setup


async def test_the_panel_left_untouched_is_not_an_abandoned_setup(
    scenario_factory, deps, analytics_events, stories
):
    """The panel carries the save of the setup it follows: its expiry adds nothing."""
    scenario = await _birthdays_set_up(scenario_factory, birthday_guild())
    told = list(stories)
    recorded = len(analytics_events)

    await scenario.expire()

    assert [event["type"] for event in analytics_events[recorded:]] == []
    assert stories == told
    await scenario.finish()


async def test_a_member_added_from_that_panel_tells_a_story_of_its_own(
    scenario_factory, deps, stories
):
    scenario = await _birthdays_set_up(scenario_factory, birthday_guild())
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await complete_member_card(scenario, LOCALE)
    await scenario.expire()

    results = [(story[2], story[3]) for story in stories]
    assert [result for result, _ in results[:2]] == ["in progress", "saved"]
    assert results[2:] == [("in progress", ()), ("added", ("➕ item added",))], (
        "the panel's story opens with its first save and ends as that save"
    )
    await scenario.finish()


async def test_a_panel_that_cannot_be_read_leaves_the_activation_embed(
    scenario_factory, deps, stories, monkeypatch
):
    """The settings are saved either way: when the feature cannot be read again,
    the activation embed stays and no panel follows it."""
    feature = feature_for(BIRTHDAYS)
    reads = {"count": 0}
    original = feature.open

    async def open_once(context):
        reads["count"] += 1
        if reads["count"] > 1:
            raise RuntimeError("the database is away")
        return await original(context)

    monkeypatch.setattr(feature, "open", open_once)
    scenario = await _birthdays_set_up(scenario_factory, birthday_guild())

    activated = ml("commands.responses.enabled.title", locale=LOCALE)
    shown = [(event.get("embed") or {}).get("title") for event in scenario.outputs]
    assert activated in shown, "the activation embed stays"
    assert not [
        session for session in scenario.sessions if session.mode.kind == "manage"
    ], "no panel is sent"
    assert [story[2] for story in stories] == ["in progress", "saved"]
    await scenario.finish()

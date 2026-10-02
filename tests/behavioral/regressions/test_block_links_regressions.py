"""Regressions: block_links redesign (2026-07-27).
"""
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "123456789"


@pytest.mark.shared_contract("manager_form")
async def test_manager_add_works_on_documents_without_the_composition_envelope(
        scenario_factory, deps):
    """What broke (caught before release by the consumer contract): when
    block_links joined COMPOSITION_COMMANDS_LIST, Manager.handle_add_item_button
    did a raw `cogs[composition_key]["values"]` lookup that raised KeyError
    for every legacy/empty document, so the manager panel crashed on open.

    Guaranteed behavior: the manager renders (with the Add button) for any
    composition command whose document lacks the composition envelope."""
    from app.services.block_links import normalize_block_links_config

    legacy = {"guild_id": GUILD_ID, "enabled": True,
              "allowed_links": ["Youtube"], "answer": "x"}
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(legacy)
    )
    scenario.expect_message(kind="send", ephemeral=True)
    scenario.expect_component(label_or_action=ml("buttons.add.label", locale="pt-br"))

    raw_without_envelope = {"guild_id": "2", "enabled": True}
    bare = await scenario_factory(locale="pt-br").start_manager(
        "block_links", raw_without_envelope
    )
    bare.expect_message(kind="send", ephemeral=True)


@pytest.mark.shared_contract("form_engine")
async def test_options_label_mapping_keeps_boolean_gates_untouched(scenario_factory):
    """What broke: nothing — this pins the boundary of the label<->raw
    mapping added for styled options steps. Gate steps with `style: boolean`
    (birthday register_now, block_links add_custom) must keep persisting raw
    True/False, or their conditions and boolean rendering would break.

    Guaranteed behavior: a boolean-styled gate answer stays raw."""
    from tests.behavioral.scenarios.test_reminders_birthday_flow import (
        _complete_global_card,
    )

    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)
    await scenario.click("Depois")                # register_now = False (raw)

    gate = scenario.answers["register_now"]
    assert gate in (False, "False"), f"boolean gate must persist raw, got {gate!r}"
    await scenario.finish()


@pytest.mark.shared_contract("form_engine")
async def test_failed_validation_in_first_composition_step_does_not_skip_it(
        scenario_factory):
    """What broke (found by the behavioral suite during the redesign): when
    the FIRST step of a composition failed validation, the on-screen buttons
    still belonged to the OUTER form; clicking them saved an EMPTY
    composition and skipped ahead, abandoning the sub-form and the user's
    retry path. Shared fix in Form._handle_after_step (composition guard).

    Guaranteed behavior: re-clicking the gate after a failed entry re-opens
    the entry modal instead of skipping the composition."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click("Sim")
    await scenario.submit_modal({"Digite o link ou site": "não é link"})

    await scenario.click("Sim")                   # retry through the gate
    scenario.expect_modal(title_contains="Link ou Site")
    await scenario.finish()


@pytest.mark.shared_contract("manager_form")
async def test_edit_dropdown_names_a_composition_entry_by_its_configuration_title():
    """What broke (reported from a live Discord test): the Editar dropdown of
    block_links listed a saved link as the option label, formatted through the
    `code` style. Discord never renders markdown inside a select, so the user
    saw literal backticks (`` `twitch.tv/jway` ``) where a configuration name
    belonged.

    Shared behavior affected: the edit picker options
    (app/settings/form/actions/manage.py, edit_options), consumed by every command with
    a composition (block_links today, twitch/youtube/birthday through the
    same screen).

    Guaranteed behavior: the option label is the configuration title with its
    position, the stored value identifies the entry in the option description,
    and no markdown leaks into either."""
    from app.settings.form.form_yaml import registry
    from app.settings.form.manager import edit_options

    cogs = {
        "enabled": True,
        "mode": "block_all",
        "custom_links": {
            "style": "composition",
            "values": [
                {
                    "link": {
                        "value": "twitch.tv/jway",
                        "title": "Link ou Site",
                        "style": "code",
                    },
                    "match_type": {
                        "value": "🌐 Todos os links desse site",
                        "_raw_value": "domain",
                    },
                }
            ],
        },
    }

    options = edit_options(registry.get("block_links"), cogs, "pt-br")
    entry = next(
        option for option in options if option.value.startswith("custom_links$")
    )

    assert "`" not in entry.label, (
        f"markdown must never reach a select option label, got {entry.label!r}"
    )
    assert entry.label == "Seus Links #1", (
        f"the label must name the configuration, got {entry.label!r}"
    )
    assert entry.description == "twitch.tv/jway", (
        "the stored value must still identify the entry, in the description"
    )


@pytest.mark.shared_contract("form_engine")
async def test_two_changes_in_a_row_on_the_same_screen_both_save(
        scenario_factory, deps):
    """What broke (reported from a live Discord test, 2026-09-16): on the
    Exceptions screen the first popular website turned on, and every click
    after it did nothing at all — the screen then refused even Voltar.

    The adapter reported every commit back to the engine as the event
    `commit:<session id>`, and the engine drops an event it has already seen.
    Until a commit closed the session, an id per session was unique enough; a
    screen that stays open commits many times, so the second outcome was
    dropped as a duplicate and the session stayed COMMITTING, which rejects
    every later click as busy.

    Shared behavior affected: Runtime._commit (app/settings/discord/
    entrypoints.py), the single path that feeds a commit's outcome back into
    the engine for every command.

    Guaranteed behavior: consecutive changes on a screen that keeps itself
    each save, and the screen goes on answering afterwards."""
    from tests.behavioral.golden.paths.block_links import ENABLED
    from tests.behavioral.golden.paths.common import GUILD_ID, seed_document
    from tests.behavioral.harness.locators import clickable_items, codec_target

    seed_document(deps, "block_links", ENABLED)
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")
    await scenario.click("edit:group:exceptions")

    await scenario.click("toggle:allowed_links=spotify.com")
    await scenario.click("toggle:allowed_links=twitter.com")

    saved = scenario.get_persisted("guild", "block_links", {"guild_id": GUILD_ID})
    assert saved["allowed_links"]["values"] == [
        "youtube.com", "twitch.tv", "spotify.com", "twitter.com"
    ], "every click on the screen must save, not only the first"

    await scenario.click("back")
    busy = ml("commands.form-notices.busy", locale="pt-br")
    said_busy = [
        event for event in scenario.outputs
        if busy and busy in (event.get("content") or "")
    ]
    assert not said_busy, "a screen that saved must not answer the next click busy"
    targets = [codec_target(item) for item in clickable_items(scenario.current_message)]
    assert not [target for target in targets if (target or "").startswith("toggle:")], (
        "Voltar must leave the screen and draw the panel"
    )
    await scenario.finish()


@pytest.mark.shared_contract("form_engine")
async def test_a_change_that_keeps_its_screen_never_says_it_is_thinking(
        scenario_factory, deps):
    """What broke (same live test): every popular website clicked left an
    ephemeral "Keiko is thinking..." message stuck on screen forever.

    The executor defers with the thinking state before an `edit` commit,
    because the edit of a step ends in a final message that answers it. A
    change that keeps its screen writes under the same kind and answers by
    editing the message instead, so nothing ever resolved the thinking state.

    Shared behavior affected: Executor._execute (app/settings/discord/
    executor.py), which runs the Commit effect of every command.

    Guaranteed behavior: a quiet commit answers its click without ever
    showing the thinking state."""
    from tests.behavioral.golden.paths.block_links import ENABLED
    from tests.behavioral.golden.paths.common import seed_document

    seed_document(deps, "block_links", ENABLED)
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")
    await scenario.click("edit:group:exceptions")

    await scenario.click("toggle:allowed_links=spotify.com")

    thinking = [
        event for event in scenario.outputs
        if event["kind"] == "defer" and event.get("thinking")
    ]
    assert not thinking, (
        f"a click that redraws its own screen must not say it is thinking: "
        f"{thinking}"
    )
    await scenario.finish()

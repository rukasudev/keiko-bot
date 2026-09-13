"""Behavioral scenarios: editing an already-saved configuration.

Runs the real EditCommand view: manager -> the section's Editar -> the form
re-runs prefilled for that step only -> Manager.update_command merges the
change into the persisted document.
"""
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

LEGACY_COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_links": ["Youtube"],
    "answer": "Resposta antiga",
}


async def test_edit_card_updates_answer_and_upgrades_legacy_shape(
        scenario_factory, deps):
    """A legacy (pre-redesign) document is edited through the new card: the
    card hydrates from the translated config, only the edited field changes
    meaningfully, and untouched keys survive."""
    from app.services.block_links import normalize_block_links_config

    deps.mongo_client.guild["block_links"].insert_one(dict(LEGACY_COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(LEGACY_COG)
    )

    await scenario.click("section:link_settings")     # the section's own pencil

    scenario.expect_message(components_v2=True)
    assert scenario.card_state["mode"] == "block_all"
    assert scenario.card_state["allowed_links"] == ["youtube.com"], \
        "legacy labels must hydrate as translated domains"
    assert scenario.card_state["answer"] == "Resposta antiga"

    await scenario.click("customize:2")               # answer modal-input
    await scenario.submit_modal({"resposta": "Resposta nova"})
    await scenario.click("done")

    scenario.expect_message(
        title_contains=ml("commands.command-events.edited.title", locale="pt-br"),
    )
    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID},
        {"answer": "Resposta nova", "mode": "block_all"},
    )
    # Untouched keys survive the merge; quick-picks got upgraded to domains.
    assert document["allowed_chats"]["values"] == "100"
    assert document["allowed_links"]["values"] == ["youtube.com"]


async def test_edit_conditional_step_is_hidden_when_condition_unmet(
        scenario_factory, deps):
    """The edit picker leaves out steps whose `when` the saved document does
    not satisfy (register_now=false hides the composition for birthday)."""
    from app.forms.definitions.registry import registry
    from app.forms.kinds.manage import edit_options

    options = edit_options(
        registry.get("reminders_birthday"),
        {"register_now": False, "reminders_birthday": {"values": []}},
        "pt-br",
    )
    option_values = [option.value for option in options]
    assert "reminders_birthday" not in option_values, (
        "composition step must be hidden when register_now condition is unmet"
    )

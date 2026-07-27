"""Behavioral scenarios: editing an already-saved configuration.

Runs the real EditCommand view: manager -> Editar -> step Select -> the
form re-runs prefilled for that step only -> Manager.update_command merges
the change into the persisted document.
"""
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_links": ["Youtube"],
    "answer": "Resposta antiga",
}


async def test_edit_single_step_updates_only_that_field(scenario_factory, deps):
    deps.mongo_client.guild["block_links"].insert_one(dict(COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(COG)
    )

    await scenario.click(ml("buttons.edit.label", locale="pt-br"))
    await scenario.select_option("answer")            # step picker (EditCommand)

    scenario.expect_modal(title_contains="Resposta ao bloquear links")
    # The modal must come prefilled with the saved answer.
    modal = scenario.store.pending_modal
    assert any(
        getattr(child, "default", None) == "Resposta antiga"
        for child in modal.children
    ), "edit modal should be prefilled with the persisted value"

    await scenario.submit_modal({"Digite minha resposta": "Resposta nova"})

    scenario.expect_message(
        title_contains=ml("commands.command-events.edited.title", locale="pt-br"),
    )
    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"answer": "Resposta nova"}
    )
    # Editing one step must not clobber the other saved fields.
    assert document["allowed_links"] == ["Youtube"]
    assert document["allowed_chats"]["values"] == "100"


async def test_edit_conditional_step_is_hidden_when_condition_unmet(
        scenario_factory, deps):
    """EditCommand filters steps whose YAML condition is not met by the
    saved config (register_now=false hides the composition for birthday)."""
    from app.views.edit import EditCommand

    edit_view = EditCommand(
        "reminders_birthday",
        {"register_now": False, "reminders_birthday": {"values": []}},
        "pt-br",
        callback=None,
    )
    select = edit_view.children[0]
    option_values = [option.value for option in select.options]
    assert "reminders_birthday" not in option_values, (
        "composition step must be hidden when register_now condition is unmet"
    )

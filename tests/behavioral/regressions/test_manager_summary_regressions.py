"""Regressions: manager summary rendering for commands that share the
manager infrastructure.
"""
import pytest

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]


async def test_manager_form_keeps_rendering_existing_config_for_all_consumers(
        scenario_factory):
    """What broke: while developing the birthday-reminder flow, shared
    summary rendering (`parse_settings_with_database_values` in
    app/services/utils.py) was changed and stopped resolving titles for
    keys nested inside `multi_select.selects[]`. The birthday flow kept
    working; commands like /moderations default roles silently lost their
    saved configuration in the manager panel and the break was only found
    by manually running those commands in Discord.

    Guaranteed behavior: a command whose values live in nested multi_select
    keys must keep showing every saved value, with its localized label and
    proper mention formatting, in the manager summary.
    """
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "default_roles",
        {
            "guild_id": "123456789", "enabled": True,
            "default_roles": {"style": "role", "values": "201"},
            "default_roles_bot": {"style": "role", "values": ["202", "203"]},
        },
    )
    scenario.expect_configuration_values(
        "Cargos para Membros", "<@&201>",
        "Cargos para Bots", "<@&202>", "<@&203>",
    )


async def test_adding_a_new_consumer_must_not_drop_other_forms_saved_values(
        scenario_factory):
    """What broke: same incident as above, seen from the other side — the
    NEW consumer (birthday) worked, so its tests passed, while EXISTING
    consumers regressed unnoticed.

    Guaranteed behavior: the representative consumers of the manager view
    render their saved values side by side in one process, so a change that
    helps one consumer but breaks another fails here immediately.
    """
    roles = await scenario_factory(locale="pt-br").start_manager(
        "default_roles",
        {"guild_id": "1", "enabled": True,
         "default_roles": {"style": "role", "values": "201"}},
    )
    links = await scenario_factory(locale="pt-br").start_manager(
        "block_links",
        {"guild_id": "2", "enabled": True,
         "allowed_chats": {"style": "channel", "values": "100"},
         "allowed_links": ["Youtube"]},
    )
    roles.expect_configuration_values("<@&201>")
    links.expect_configuration_values("<#100>", "Youtube")

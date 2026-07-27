"""Shared-component contract: the manager view must keep working for every
command that consumes it.

`send_command_manager_message` (app/services/moderations.py) + the summary
renderers in app/services/utils.py are shared infrastructure: a change made
for ONE command has historically broken the saved-configuration display of
the OTHERS (see tests/behavioral/regressions/). Each consumer below uses
its real production YAML and a cog document in the real persisted shape
(the same shape the behavioral scenarios verified end-to-end).
"""
import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

# Documents in the REAL persisted shape produced by Form._parse_responses_to_cog
# (flat values or {style, values} envelopes; compositions as {style, values[]}).
CONSUMERS = [
    pytest.param(
        "default_roles",
        {
            "guild_id": GUILD_ID, "enabled": True,
            "default_roles": {"style": "role", "values": "201"},
            "default_roles_bot": {"style": "role", "values": ["202"]},
        },
        ["Cargos para Membros", "<@&201>", "Cargos para Bots", "<@&202>"],
        id="default_roles-nested-multi-select",
    ),
    pytest.param(
        "block_links",
        {
            "guild_id": GUILD_ID, "enabled": True,
            "allowed_chats": {"style": "channel", "values": "100"},
            "allowed_roles": {"style": "role", "values": "201"},
            "allowed_links": ["Youtube", "Spotify"],
            "answer": "Nada de links aqui! :p",
        },
        ["<#100>", "<@&201>", "Youtube", "Nada de links aqui! :p"],
        id="block_links-options-and-modal",
    ),
    pytest.param(
        "notifications_twitch",
        {
            "guild_id": GUILD_ID, "enabled": True,
            "notifications": {
                "style": "composition",
                "values": [{
                    "channel": {"value": "100", "style": "channel"},
                    "streamer": {"value": "gaules"},
                }],
            },
        },
        ["<#100>", "gaules"],
        id="notifications_twitch-composition",
    ),
]


@pytest.mark.parametrize("command_key,cog_data,expected_values", CONSUMERS)
async def test_manager_form_renders_existing_configuration(
        command_key, cog_data, expected_values, scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager(
        command_key, cog_data
    )
    scenario.expect_message(kind="send", ephemeral=True)
    scenario.expect_configuration_values(*expected_values)

    for button_key in ("edit", "pause", "disable", "help"):
        scenario.expect_component(
            label_or_action=ml(f"buttons.{button_key}.label", locale="pt-br")
        )


async def test_manager_form_renders_birthday_via_real_settings_provider(
        scenario_factory, deps):
    """Birthday is the one consumer with a custom settings_provider; seed the
    DB through the real data layer and build cog_data with the real provider
    input function."""
    from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
    from app.services.reminders_birthdays import birthday_manager_cog_data

    upsert_birthday_config(
        GUILD_ID, "100", False,
        timezone="America/Sao_Paulo", notification_time="08:00",
    )
    upsert_birthday_item(GUILD_ID, "555", "05-12")

    cog_data = birthday_manager_cog_data(GUILD_ID)
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "reminders_birthday", cog_data
    )
    scenario.expect_message(kind="send", ephemeral=True)
    scenario.expect_configuration_values("<#100>", "08:00")


async def test_manager_form_supports_empty_configuration(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", {"guild_id": GUILD_ID, "enabled": True}
    )
    scenario.expect_message(kind="send", ephemeral=True)


async def test_one_command_config_does_not_leak_into_another(scenario_factory):
    roles_scenario = await scenario_factory(locale="pt-br").start_manager(
        "default_roles",
        {
            "guild_id": GUILD_ID, "enabled": True,
            "default_roles": {"style": "role", "values": "201"},
        },
    )
    twitch_scenario = await scenario_factory(locale="pt-br").start_manager(
        "notifications_twitch",
        {
            "guild_id": GUILD_ID, "enabled": True,
            "notifications": {
                "style": "composition",
                "values": [{"streamer": {"value": "gaules"}}],
            },
        },
    )

    roles_description = (roles_scenario.outputs[-1].get("embed") or {}).get("description", "")
    twitch_description = (twitch_scenario.outputs[-1].get("embed") or {}).get("description", "")
    assert "<@&201>" in roles_description and "gaules" not in roles_description
    assert "gaules" in twitch_description and "<@&201>" not in twitch_description

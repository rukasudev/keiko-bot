"""Shared contract: the manager panel.

`ManagerPanelView` (`app/views/manager_panel.py`) is what every command's
manager renders as, through `build_command_manager_message`. Four things must
hold whatever the command: every saved value stays readable, sections come from
the YAML that owns them, a setting no step owns is still editable, and the
container stays inside Discord's Components V2 limits — a limit breach is
rejected by the API at runtime, never by a unit test.
"""
import pytest

import discord

from app.constants import Commands as command_constants
from app.services.utils import (
    parse_form_yaml_to_dict,
    parse_settings_with_database_values,
    resolve_form_settings_icons,
)
from app.views.manager_panel import row_value

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_panel")]

GUILD_ID = "123456789"

BLOCK_LINKS_COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "mode": "block_all",
    "answer": "Nada de links por aqui!",
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_roles": {"style": "role", "values": "200"},
    "custom_links": {
        "style": "composition",
        "values": [
            {"link": {"value": "twitch.tv/jway"}, "match_type": {"value": "domain"}},
        ],
    },
}


async def test_the_panel_shows_the_saved_values(scenario_factory, deps):
    """The whole point of the panel: what is configured must be readable."""
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS_COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(BLOCK_LINKS_COG)
    )

    scenario.expect_configuration_values(
        "youtube.com",               # popular websites
        "<#100>",                    # allowed channels
        "twitch.tv/jway",            # composition entry
    )


async def test_the_panel_keeps_the_additional_info_section(scenario_factory, deps):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS_COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(BLOCK_LINKS_COG)
    )

    from app.services.block_links import _manager_info, _manager_info_title

    summary = scenario.rendered_summary
    assert _manager_info_title("pt-br") in summary, \
        "the context-menu tip must keep its own heading"
    assert _manager_info("pt-br") in summary


def test_every_settings_row_carries_the_icon_declared_in_its_yaml():
    """Each row's icon comes from the YAML that owns the setting, so a command
    changes its panel by editing configuration, never Python."""
    form_steps = list(parse_form_yaml_to_dict("block_links"))
    rows = parse_settings_with_database_values(
        dict(BLOCK_LINKS_COG), form_steps, "pt-br"
    )
    declared = resolve_form_settings_icons(form_steps)

    assert declared, "block_links declares icons in its YAML"
    for row in rows:
        key = row.get("key")
        if key in declared:
            assert row.get("icon") == declared[key], (
                f"row {key!r} rendered with {row.get('icon')!r}, YAML declares "
                f"{declared[key]!r}"
            )
        else:
            assert row.get("icon"), f"row {key!r} has no icon and no fallback"


def _crowded_cog() -> dict:
    """Worst realistic case: a composition at its cap, every entry long."""
    cap = command_constants.COMPOSITION_MAX_LENGTH[command_constants.BLOCK_LINKS_KEY]
    entries = [
        {"link": {"value": f"exemplo-muito-longo-{index:02d}.com/caminho/inteiro"},
         "match_type": {"value": "exact"}}
        for index in range(cap)
    ]
    return {
        **BLOCK_LINKS_COG,
        "answer": "Opa! " + ("Nada de links aqui, combinado? " * 40),
        "allowed_links": {
            "style": "bullet",
            "values": [f"site-popular-numero-{index:02d}.com" for index in range(20)],
        },
        "custom_links": {"style": "composition", "values": entries},
    }


async def test_the_panel_stays_within_components_v2_limits(scenario_factory, deps):
    """Discord answers to 40 components and 4000 characters of text, and
    rejects the whole message when either is crossed — a server with a full
    list of links has to fit."""
    from tests.behavioral.harness.locators import walk_items

    cog = _crowded_cog()
    deps.mongo_client.guild["block_links"].insert_one(dict(cog))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", cog
    )

    items = list(walk_items(scenario.current_message.view))
    assert len(items) <= 40, f"{len(items)} components exceed Discord's limit of 40"
    total = sum(len(item.content or "") for item in items
                if isinstance(item, discord.ui.TextDisplay))
    assert total <= 4000, f"{total} chars of text exceed Discord's limit of 4000"


def test_a_composition_entry_shows_one_line_per_field():
    """An entry has several fields; run together on one line they stop being
    readable, so each field keeps its own labelled line under a numbered
    header."""
    row = {
        "title": "Seus Links", "style": "composition",
        "value": [
            {"link": {"value": "twitch.tv/jway", "style": "code",
                      "title": "Link ou Site"},
             "match_type": {"value": "🌐 Todos os links desse site",
                            "title": "Qual o Alcance da Regra?"}},
        ],
    }

    text, is_block = row_value(row, "pt-br")

    assert is_block
    assert text.splitlines() == [
        "**#1**",
        "**Link ou Site:** `twitch.tv/jway`",
        # A title that already asks a question never gets a second colon.
        "**Qual o Alcance da Regra?** 🌐 Todos os links desse site",
    ]


def test_a_saved_option_value_drops_the_emoji_its_button_needs():
    """The option label carries the emoji so button and explanation match; on
    the panel that emoji is decoration competing with the section's own."""
    text, _ = row_value(
        {"title": "Modo de bloqueio", "value": "✅ Permitir todos, bloquear alguns"},
        "pt-br",
    )

    assert text == "Permitir todos, bloquear alguns"


def test_settings_rows_are_grouped_by_the_step_that_owns_them():
    """The panel's sections come from the YAML structure that already exists —
    a card owns its fields, a multi-select owns its selects."""
    form_steps = list(parse_form_yaml_to_dict("block_links"))
    rows = parse_settings_with_database_values(dict(BLOCK_LINKS_COG), form_steps, "pt-br")
    groups = {row["key"]: row.get("group") for row in rows}

    assert groups["mode"] == groups["answer"] == groups["allowed_links"] == "link_settings"
    assert groups["allowed_chats"] == groups["allowed_roles"] == "permissions"
    assert groups["custom_links"] == "custom_links"
    assert all(row.get("group_title") for row in rows), \
        "every grouped row carries its localized section title"


async def test_a_section_never_repeats_the_panel_title(scenario_factory, deps):
    """default_roles is one step whose title IS the command's: drawn as a
    heading it would print the same words twice, one size apart."""
    cog = {
        "guild_id": GUILD_ID, "enabled": True,
        "default_roles": {"style": "role", "values": "201"},
        "default_roles_bot": {"style": "role", "values": ["202"]},
    }
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "default_roles", cog
    )

    summary = scenario.rendered_summary
    assert summary.count("Cargos Padrão") == 1, (
        f"the command title is repeated as a section heading:\n{summary}"
    )
    scenario.expect_configuration_values("<@&201>", "<@&202>")


async def test_settings_no_yaml_step_owns_keep_the_global_edit_button(
    scenario_factory, deps
):
    """Birthday builds its rows in a `settings_provider`, so no section can
    carry its own pencil. Dropping the global Edit button there would leave
    the command with no way into the edit flow at all."""
    from app.components.buttons import EditButton
    from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
    from app.services.reminders_birthdays import birthday_manager_cog_data
    from app.views.manager_panel import SectionEditButton
    from tests.behavioral.harness.locators import walk_items

    upsert_birthday_config(GUILD_ID, "100", False,
                           timezone="America/Sao_Paulo", notification_time="08:00")
    upsert_birthday_item(GUILD_ID, "555", "05-12")
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "reminders_birthday", birthday_manager_cog_data(GUILD_ID)
    )

    items = list(walk_items(scenario.current_message.view))
    assert not [item for item in items if isinstance(item, SectionEditButton)], \
        "a settings_provider owns no step, so no section can be edited on its own"
    assert [item for item in items if isinstance(item, EditButton)], \
        "the global Edit button is the only way in and must stay"
    scenario.expect_configuration_values("<#100>", "08:00")

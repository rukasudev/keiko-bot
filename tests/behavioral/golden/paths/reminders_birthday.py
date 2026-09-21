"""Birthday reminders: the reminder settings card, the "add the first
birthday?" gate, the member sub-form (member picker and a card with month,
day, message and image), and a manager with its own persistence.

A paused birthday feature has no Unpause: the service reads the moderation
flag and reopens the setup form instead of the manager, so the lifecycle
here records that reopening in place of `manager_unpause`.
"""

from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
from app.services.moderations import update_moderations_by_guild
from tests.behavioral.golden.paths import golden_path
from tests.behavioral.golden.paths.common import (
    GUILD_ID,
    cancel_discard,
    cancel_keep,
    label,
    register_lifecycle,
)
from tests.mocks.discord import create_guild, create_member

FORM = "reminders_birthday"

DAY_FIELD = {"pt-br": "Dia", "en-us": "Day"}


def _guild():
    guild = create_guild()
    create_member(guild, id=777, name="Amiga", roles=["Member"])
    return guild


def _seed_config(deps, enabled=True, members=("555",)):
    upsert_birthday_config(
        GUILD_ID,
        "100",
        False,
        timezone="America/Sao_Paulo",
        notification_time="08:00",
    )
    for index, user_id in enumerate(members):
        upsert_birthday_item(GUILD_ID, user_id, f"{5 + index:02d}-12")
    update_moderations_by_guild(GUILD_ID, FORM, enabled)


async def _open(scenario_factory, locale):
    return await scenario_factory(locale=locale, guild=_guild()).start_command(FORM)


async def _complete_settings_card(scenario):
    await scenario.click("customize:0")
    await scenario.select_option("general")
    await scenario.click("customize:1")
    await scenario.select_option("America/Sao_Paulo")
    await scenario.click("customize:2")
    await scenario.click("08:00")
    await scenario.click("done")


async def _complete_member_card(scenario, locale, month="05", day="12"):
    await scenario.click("customize:0")
    await scenario.select_option(month)
    await scenario.click("customize:1")
    await scenario.submit_modal({DAY_FIELD[locale]: day})
    await scenario.click("done")


async def _to_member_card(scenario_factory, locale):
    scenario = await _open(scenario_factory, locale)
    await scenario.confirm()
    await _complete_settings_card(scenario)
    await scenario.click(label("yes", locale))
    await scenario.select_option("Amiga")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await _to_member_card(scenario_factory, locale)
    await _complete_member_card(scenario, locale)
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    scenario = await _to_member_card(scenario_factory, locale)
    await scenario.click("customize:0")
    await scenario.select_option("02")
    await scenario.click("customize:1")
    await scenario.submit_modal({DAY_FIELD[locale]: "31"})
    await scenario.click("customize:1")
    await scenario.submit_modal({DAY_FIELD[locale]: "28"})
    await scenario.click("done")
    return scenario


@golden_path(FORM, "setup_back")
async def setup_back(scenario_factory, deps, locale):
    scenario = await _open(scenario_factory, locale)
    await scenario.confirm()
    await _complete_settings_card(scenario)
    await scenario.go_back()
    await scenario.click("customize:1")
    await scenario.select_option("America/New_York")
    await scenario.click("done")
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await _open(scenario_factory, locale)
    await scenario.confirm()
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await _open(scenario_factory, locale)
    await scenario.confirm()
    await cancel_discard(scenario, locale)
    return scenario


async def _open_manager(scenario_factory, deps, locale, members=("555",)):
    _seed_config(deps, members=members)
    return await scenario_factory(locale=locale, guild=_guild()).start_command(FORM)


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await _open_manager(scenario_factory, deps, locale)
    await scenario.click("section:birthday_config")
    await scenario.click("customize:2")
    await scenario.click("10:00")
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await _open_manager(scenario_factory, deps, locale)
    await scenario.click("add")
    await scenario.select_option("Amiga")
    await scenario.confirm()
    await _complete_member_card(scenario, locale, month="11", day="03")
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await _open_manager(
        scenario_factory, deps, locale, members=("555", "777")
    )
    await scenario.click("remove")
    await scenario.select_option("reminders_birthday")
    await scenario.select_option("Tester")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "manager_paused_reopens_setup")
async def manager_paused_reopens_setup(scenario_factory, deps, locale):
    _seed_config(deps, enabled=False)
    return await scenario_factory(locale=locale, guild=_guild()).start_command(FORM)


register_lifecycle(
    FORM,
    seed_enabled=lambda deps: _seed_config(deps, enabled=True),
    seed_paused=lambda deps: _seed_config(deps, enabled=False),
    with_unpause=False,
)

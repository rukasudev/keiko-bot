"""Birthday reminders: the reminder settings card and the review in the setup;
the member sub-form (member picker and a card with month, day, message and
image) from the panel once saved (bd-3), and a manager with its own
persistence.

There is no `setup_back` path since bd-3: the settings card follows the intro
and the review has no Back.

A paused birthday feature opens its manager, like every other feature, so
the lifecycle records its Unpause (ux-80).
"""

from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
from app.services.moderations import update_moderations_by_guild
from tests.behavioral.golden.paths import golden_path
from tests.behavioral.golden.paths.common import (
    GUILD_ID,
    cancel_discard,
    cancel_keep,
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
    await scenario.select_option("general", target="channel")
    await scenario.select_option("America/Sao_Paulo", target="timezone")
    await scenario.select_option("08:00", target="notification_time")
    await scenario.click("done")


async def _type_day(scenario, day, locale="pt-br"):
    """The day typed in the modal of the card's day section."""
    await scenario.click("customize:2")
    await scenario.submit_modal({DAY_FIELD[locale]: day})


async def _complete_member_card(scenario, locale, month="05", day="12"):
    await scenario.select_option(month, target="month")
    await _type_day(scenario, day, locale)
    await scenario.click("done")


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await _open(scenario_factory, locale)
    await scenario.confirm()
    await _complete_settings_card(scenario)
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    scenario = await _open(scenario_factory, locale)
    await scenario.confirm()
    await scenario.click("done")
    await _complete_settings_card(scenario)
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await _open(scenario_factory, locale)
    await scenario.confirm()
    await scenario.select_option("general", target="channel")
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await _open(scenario_factory, locale)
    await scenario.confirm()
    await scenario.select_option("general", target="channel")
    await cancel_discard(scenario, locale)
    return scenario


async def _open_manager(scenario_factory, deps, locale, members=("555",)):
    _seed_config(deps, members=members)
    return await scenario_factory(locale=locale, guild=_guild()).start_command(FORM)


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await _open_manager(scenario_factory, deps, locale)
    await scenario.click("edit:group:settings")
    await scenario.click("section:birthday_config")
    await scenario.select_option("10:00", target="notification_time")
    await scenario.click("set:mention_everyone=true")
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await _open_manager(scenario_factory, deps, locale)
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await scenario.select_option("02", target="month")
    await _type_day(scenario, "31", locale)
    await _type_day(scenario, "28", locale)
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await _open_manager(
        scenario_factory, deps, locale, members=("555", "777")
    )
    await scenario.click("search")
    await scenario.select_option("Tester")
    await scenario.click("remove_this:reminders_birthday$0")
    await scenario.click("remove_items")
    return scenario


@golden_path(FORM, "manager_search")
async def manager_search(scenario_factory, deps, locale):
    scenario = await _open_manager(
        scenario_factory, deps, locale, members=("555", "777")
    )
    await scenario.click("search")
    await scenario.select_option("Amiga")
    await scenario.go_back()
    return scenario


@golden_path(FORM, "manager_settings")
async def manager_settings(scenario_factory, deps, locale):
    scenario = await _open_manager(scenario_factory, deps, locale)
    await scenario.click("edit:group:settings")
    await scenario.go_back()
    return scenario


@golden_path(FORM, "manager_empty_list", locales=("pt-br", "en-us"))
async def manager_empty_list(scenario_factory, deps, locale):
    return await _open_manager(scenario_factory, deps, locale, members=())


@golden_path(FORM, "manager_month_pages")
async def manager_month_pages(scenario_factory, deps, locale):
    scenario = await _open_manager(
        scenario_factory, deps, locale, members=("555", "777")
    )
    await scenario.click("page:1")
    await scenario.click("page:0")
    return scenario


register_lifecycle(
    FORM,
    seed_enabled=lambda deps: _seed_config(deps, enabled=True),
    seed_paused=lambda deps: _seed_config(deps, enabled=False),
)

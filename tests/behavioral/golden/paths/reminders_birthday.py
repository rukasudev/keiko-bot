"""Birthday reminders: the reminder settings card and the review in the setup;
the member sub-form (member picker and a card with month, day, message and
image) from the panel once saved (bd-3), and a manager with its own
persistence.

There is no `setup_back` path since bd-3: the settings card follows the intro
and the review has no Back.

A paused birthday feature opens its manager, like every other feature, so
the lifecycle records its Unpause (ux-80).
"""

from tests.behavioral.flows.birthdays import (
    FORM,
    complete_settings_card,
    open_manager,
    open_setup,
    seed_config,
    type_day,
)
from tests.behavioral.flows.common import cancel_discard, cancel_keep
from tests.behavioral.golden.paths import golden_book, golden_path
from tests.behavioral.golden.paths.common import register_lifecycle

golden_book(FORM, rendered=("form.panel", "form.review"))


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await open_setup(scenario_factory, locale)
    await scenario.confirm()
    await complete_settings_card(scenario)
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    scenario = await open_setup(scenario_factory, locale)
    await scenario.confirm()
    await scenario.click("done")
    await complete_settings_card(scenario)
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await open_setup(scenario_factory, locale)
    await scenario.confirm()
    await scenario.select_option("general", target="channel")
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await open_setup(scenario_factory, locale)
    await scenario.confirm()
    await scenario.select_option("general", target="channel")
    await cancel_discard(scenario, locale)
    return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale)
    await scenario.click("edit:group:settings")
    await scenario.click("section:birthday_config")
    await scenario.select_option("10:00", target="notification_time")
    await scenario.select_option("everyone", target="mention")
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale)
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await scenario.select_option("02", target="month")
    await type_day(scenario, "31", locale)
    await type_day(scenario, "28", locale)
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await open_manager(
        scenario_factory, deps, locale, members=("555", "777")
    )
    await scenario.click("search")
    await scenario.select_option("Tester")
    await scenario.click("remove_this:reminders_birthday$0")
    await scenario.click("remove_items")
    return scenario


@golden_path(FORM, "manager_search")
async def manager_search(scenario_factory, deps, locale):
    scenario = await open_manager(
        scenario_factory, deps, locale, members=("555", "777")
    )
    await scenario.click("search")
    await scenario.select_option("Amiga")
    await scenario.go_back()
    return scenario


@golden_path(FORM, "manager_settings")
async def manager_settings(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale)
    await scenario.click("edit:group:settings")
    await scenario.go_back()
    return scenario


@golden_path(FORM, "manager_empty_list", locales=("pt-br", "en-us"))
async def manager_empty_list(scenario_factory, deps, locale):
    return await open_manager(scenario_factory, deps, locale, members=())


@golden_path(FORM, "manager_month_pages")
async def manager_month_pages(scenario_factory, deps, locale):
    scenario = await open_manager(
        scenario_factory, deps, locale, members=("555", "777")
    )
    await scenario.click("page:1")
    await scenario.click("page:0")
    return scenario


register_lifecycle(
    FORM,
    seed_enabled=lambda deps: seed_config(deps, enabled=True),
    seed_paused=lambda deps: seed_config(deps, enabled=False),
)

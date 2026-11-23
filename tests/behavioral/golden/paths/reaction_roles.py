"""Reaction roles: a list of up to three menus, each set up on one card (channel,
message already filled, mode, roles) and posted in its channel, and what a member sees
when clicking a posted menu: their own copy of it, or the reason it was refused.
"""
from tests.behavioral.flows.common import cancel_discard, cancel_keep, seed_document
from tests.behavioral.flows.reaction_roles import (
    COLOURS_MENU,
    FORM,
    PAUSED,
    add_menu,
    click_menu,
    click_role,
    edit_menu,
    menu_scenario,
    open_card,
    open_panel,
    pick_channel,
    pick_only_one,
    pick_roles,
    posted,
    seed,
    seed_paused,
    set_up_menu,
    type_message,
)
from tests.behavioral.golden.paths import golden_book, golden_path
from tests.behavioral.golden.paths.common import register_lifecycle

golden_book(FORM, rendered=("form.panel", "form.review"))


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    return await set_up_menu(scenario_factory, deps, locale, channel_name="general")


@golden_path(FORM, "setup_only_one_with_its_own_message")
async def setup_only_one_with_its_own_message(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, deps, locale)
    await pick_channel(scenario, "cores")
    await type_message(scenario, "🎨 Escolha sua cor", "Uma cor por membro!")
    await pick_only_one(scenario)
    await pick_roles(scenario, "Azul", "Verde")
    await scenario.click("done")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, deps, locale)
    await pick_channel(scenario)
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, deps, locale)
    await pick_channel(scenario)
    await cancel_discard(scenario, locale)
    return scenario


@golden_path(FORM, "member_takes_and_drops_roles", locales=("pt-br", "en-us"))
async def member_takes_and_drops_roles(scenario_factory, deps, locale):
    scenario = await set_up_menu(scenario_factory, deps, locale)
    [menu] = posted(scenario)
    await click_role(scenario, menu, "Gamer")
    await click_role(scenario, scenario.current_message, "Artista")
    await click_role(scenario, scenario.current_message, "Gamer")
    return scenario


@golden_path(FORM, "member_refused_while_paused", locales=("pt-br", "en-us"))
async def member_refused_while_paused(scenario_factory, deps, locale):
    seed_document(deps, FORM, PAUSED)
    scenario = menu_scenario(scenario_factory, locale)
    await click_menu(scenario, COLOURS_MENU, "Azul")
    return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_panel(scenario_factory, deps, locale)
    await edit_menu(scenario, 0)
    await type_message(scenario, "🎮 Jogos", "Escolha seus jogos!")
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await open_panel(scenario_factory, deps, locale)
    await add_menu(scenario, "cargos", ("Leitor",))
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await open_panel(scenario_factory, deps, locale)
    await scenario.click("remove_this:menus$1")
    await scenario.click("remove_items")
    return scenario


register_lifecycle(FORM, seed_enabled=seed, seed_paused=seed_paused)

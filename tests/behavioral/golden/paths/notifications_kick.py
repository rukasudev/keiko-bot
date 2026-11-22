"""Kick lives: the Twitch shape for Kick, up to three streamers, each set up on one
card with the Kick channel lookup as validator and its own Edit on the panel.
"""
from tests.behavioral.flows.common import cancel_discard, cancel_keep, open_manager
from tests.behavioral.flows.kick import (
    FORM,
    open_card,
    pick_channel,
    seed,
    seed_paused,
    seed_two_entries,
    submit_messages,
    submit_streamer,
)
from tests.behavioral.golden.paths import golden_book, golden_path
from tests.behavioral.golden.paths.common import register_lifecycle

golden_book(FORM, rendered=("form.streamers_panel", "form.review"))


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, deps, locale)
    await pick_channel(scenario)
    await submit_streamer(scenario, "xqcow")
    await submit_messages(scenario)
    await scenario.click("done")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, deps, locale)
    await pick_channel(scenario)
    await submit_streamer(scenario, "naoexiste")
    await submit_streamer(scenario, "xqcow")
    return scenario


@golden_path(FORM, "setup_back")
async def setup_back(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, deps, locale)
    await pick_channel(scenario)
    await submit_streamer(scenario, "xqcow")
    await pick_channel(scenario, "announcements")
    await submit_streamer(scenario, "adinross")
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


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await scenario.click("section:notifications$0")
    await pick_channel(scenario, "announcements")
    await submit_streamer(scenario, "xqcow")
    await submit_messages(scenario)
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await scenario.click("add")
    await pick_channel(scenario)
    await submit_streamer(scenario, "amouranth")
    await submit_messages(scenario)
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed_two_entries)
    await scenario.click("remove_this:notifications$0")
    await scenario.click("remove_items")
    return scenario


register_lifecycle(FORM, seed_enabled=seed, seed_paused=seed_paused)

"""StreamElements commands: one validated modal (the streamer username).

The only screen with a Cancel button is the intro; modals have no Back, so
there is no `setup_back` path. The streamer has its own Edit on the panel.
"""

from tests.behavioral.flows.common import cancel_discard, cancel_keep, open_manager
from tests.behavioral.flows.stream_elements import (
    FORM,
    known_streamers,
    seed,
    seed_paused,
    submit_streamer,
)
from tests.behavioral.golden.paths import golden_book, golden_path
from tests.behavioral.golden.paths.common import register_lifecycle

golden_book(FORM, rendered=("commands.panel", "commands.confirm"))


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    known_streamers(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await submit_streamer(scenario, "shroud")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    known_streamers(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await submit_streamer(scenario, "naoexiste")
    await scenario.click("customize:streamer")
    await submit_streamer(scenario, "shroud")
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    known_streamers(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await submit_streamer(scenario, "shroud")
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    known_streamers(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await submit_streamer(scenario, "shroud")
    await cancel_discard(scenario, locale)
    return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    known_streamers(deps)
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await scenario.click("section:streamer")
    await submit_streamer(scenario, "gaules")
    return scenario


register_lifecycle(FORM, seed_enabled=seed, seed_paused=seed_paused)

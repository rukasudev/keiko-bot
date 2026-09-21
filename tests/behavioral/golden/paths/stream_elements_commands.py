"""StreamElements commands: one validated modal (the streamer username).

The only screen with a Cancel button is the intro; modals have no Back, so
there is no `setup_back` path. The panel keeps the global Edit button (the
saved channel id belongs to no step).
"""
from tests.behavioral.golden.paths import golden_path
from tests.behavioral.golden.paths.common import (
    GUILD_ID,
    cancel_discard,
    cancel_keep,
    open_manager,
    register_lifecycle,
    seed_document,
)

FORM = "stream_elements_commands"

ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "streamer": "shroud",
    "channel_id": "5f1a2b3c4d5e6f7a8b9c0d1e",
}
PAUSED = {**ENABLED, "enabled": False}


def _known_streamers(deps):
    deps.twitch.add_user("shroud", user_id="37402112")
    deps.twitch.add_user("gaules", user_id="181077473")


async def _submit_streamer(scenario, name):
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: name})


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    _known_streamers(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await _submit_streamer(scenario, "shroud")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    _known_streamers(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await _submit_streamer(scenario, "naoexiste")
    await scenario.confirm()
    await _submit_streamer(scenario, "shroud")
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await cancel_discard(scenario, locale)
    return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    _known_streamers(deps)
    scenario = await open_manager(
        scenario_factory, deps, locale, FORM, lambda d: seed_document(d, FORM, ENABLED)
    )
    await scenario.click("edit")
    await scenario.select_option("streamer")
    await _submit_streamer(scenario, "gaules")
    return scenario


register_lifecycle(
    FORM,
    seed_enabled=lambda deps: seed_document(deps, FORM, ENABLED),
    seed_paused=lambda deps: seed_document(deps, FORM, PAUSED),
)

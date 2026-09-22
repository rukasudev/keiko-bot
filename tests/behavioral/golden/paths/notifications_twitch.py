"""Twitch notifications: a list of up to three streamers, each set up on one card
(channel, validated nick, messages already filled) with its own Edit on the panel.
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

FORM = "notifications_twitch"

MESSAGE = "@everyone {streamer} está ao vivo! {stream_link}"


def notification(channel: str, streamer: str) -> dict:
    return {
        "channel": {"value": channel, "title": "Canal de Texto", "style": "channel"},
        "streamer": {"value": streamer, "title": "Streamer"},
        "notification_messages": {"value": [MESSAGE], "title": "Mensagens de Notificação",
                                  "style": "bullet"},
    }


ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "notifications": {
        "style": "composition",
        "values": [notification("100", "gaules"), notification("102", "cellbit")],
    },
}
PAUSED = {**ENABLED, "enabled": False}


def _known_streamers(deps):
    deps.twitch.add_user("gaules", user_id="181077473")
    deps.twitch.add_user("cellbit", user_id="28579002")
    deps.twitch.add_user("shroud", user_id="37402112")


async def _card(scenario_factory, deps, locale):
    _known_streamers(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    return scenario


async def _channel(scenario, name="general"):
    await scenario.click("customize:0")
    await scenario.select_option(name)


async def _submit_streamer(scenario, name):
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: name})


async def _submit_messages(scenario):
    await scenario.click("customize:2")
    first = scenario.pending_modal_fields()[0]
    await scenario.submit_modal({first: "{streamer} entrou ao vivo: {stream_link}"})


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await _card(scenario_factory, deps, locale)
    await _channel(scenario)
    await _submit_streamer(scenario, "gaules")
    await _submit_messages(scenario)
    await scenario.click("done")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    scenario = await _card(scenario_factory, deps, locale)
    await _channel(scenario)
    await _submit_streamer(scenario, "naoexiste")
    await _submit_streamer(scenario, "gaules")
    return scenario


@golden_path(FORM, "setup_back")
async def setup_back(scenario_factory, deps, locale):
    scenario = await _card(scenario_factory, deps, locale)
    await _channel(scenario)
    await _submit_streamer(scenario, "gaules")
    await scenario.click("customize:0")
    await scenario.go_back()
    await _channel(scenario, "announcements")
    await _submit_streamer(scenario, "cellbit")
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await cancel_discard(scenario, locale)
    return scenario


def _seed(deps):
    _known_streamers(deps)
    seed_document(deps, FORM, ENABLED)


def _seed_paused(deps):
    _known_streamers(deps)
    seed_document(deps, FORM, PAUSED)


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed)
    await scenario.click("section:notifications$0")
    await _channel(scenario, "announcements")
    await _submit_streamer(scenario, "gaules")
    await _submit_messages(scenario)
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed)
    await scenario.click("add")
    await _channel(scenario)
    await _submit_streamer(scenario, "shroud")
    await _submit_messages(scenario)
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed)
    await scenario.click("remove")
    await scenario.select_option("notifications$0")
    return scenario


register_lifecycle(FORM, seed_enabled=_seed, seed_paused=_seed_paused)

"""YouTube notifications: the Twitch shape with a youtuber instead of a
streamer, up to two entries, each set up on one card with the YouTube channel
lookup as validator and its own Edit on the panel.
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

FORM = "notifications_youtube_video"

MESSAGE = "@everyone! {youtuber} acabou de postar um novo vídeo!"


def notification(channel: str, youtuber: str) -> dict:
    return {
        "channel": {"value": channel, "title": "Canal de Texto", "style": "channel"},
        "youtuber": {"value": youtuber, "title": "Youtuber"},
        "notification_messages": {"value": [MESSAGE], "title": "Mensagens de Notificação",
                                  "style": "bullet"},
    }


ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "notifications": {
        "style": "composition",
        "values": [notification("100", "pewdiepie")],
    },
}
TWO_ENTRIES = {
    **ENABLED,
    "notifications": {
        "style": "composition",
        "values": [notification("100", "pewdiepie"), notification("102", "mrbeast")],
    },
}
PAUSED = {**ENABLED, "enabled": False}


def _known_channels(deps):
    deps.youtube.add_channel("UC-lHJZR3Gqxm24_Vd_AJ5Yw", "PewDiePie", custom_url="@pewdiepie")
    deps.youtube.add_channel("UCX6OQ3DkcsbYNE6H8uQQuVA", "MrBeast", custom_url="@mrbeast")


async def _card(scenario_factory, deps, locale):
    _known_channels(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    return scenario


async def _channel(scenario, name="general"):
    await scenario.click("customize:0")
    await scenario.select_option(name)


async def _submit_youtuber(scenario, name):
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: name})


async def _submit_messages(scenario):
    await scenario.click("customize:2")
    first = scenario.pending_modal_fields()[0]
    await scenario.submit_modal({first: "{youtuber} postou: {video_link}"})


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await _card(scenario_factory, deps, locale)
    await _channel(scenario)
    await _submit_youtuber(scenario, "pewdiepie")
    await _submit_messages(scenario)
    await scenario.click("done")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    scenario = await _card(scenario_factory, deps, locale)
    await _channel(scenario)
    await _submit_youtuber(scenario, "naoexiste")
    await _submit_youtuber(scenario, "pewdiepie")
    return scenario


@golden_path(FORM, "setup_back")
async def setup_back(scenario_factory, deps, locale):
    scenario = await _card(scenario_factory, deps, locale)
    await _channel(scenario)
    await _submit_youtuber(scenario, "pewdiepie")
    await scenario.click("customize:0")
    await scenario.go_back()
    await _channel(scenario, "announcements")
    await _submit_youtuber(scenario, "mrbeast")
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
    _known_channels(deps)
    seed_document(deps, FORM, ENABLED)


def _seed_paused(deps):
    _known_channels(deps)
    seed_document(deps, FORM, PAUSED)


def _seed_two_entries(deps):
    _known_channels(deps)
    seed_document(deps, FORM, TWO_ENTRIES)


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed)
    await scenario.click("section:notifications$0")
    await _channel(scenario, "announcements")
    await _submit_youtuber(scenario, "pewdiepie")
    await _submit_messages(scenario)
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed)
    await scenario.click("add")
    await _channel(scenario)
    await _submit_youtuber(scenario, "mrbeast")
    await _submit_messages(scenario)
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed_two_entries)
    await scenario.click("remove")
    await scenario.select_option("notifications$0")
    return scenario


register_lifecycle(FORM, seed_enabled=_seed, seed_paused=_seed_paused)

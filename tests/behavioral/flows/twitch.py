"""Twitch notifications: the saved notices, the streamers the fake Twitch knows, and the
card's steps (the channel, the streamer, the messages)."""

from tests.behavioral.flows import common
from tests.behavioral.flows.common import GUILD_ID, seed_document

FORM = "notifications_twitch"

MESSAGE = "{streamer} está ao vivo! {stream_link}"
EVERYONE = {"value": "@everyone", "title": "Mencionar", "_raw_value": "everyone"}


def notification(channel: str, streamer: str) -> dict:
    """One saved notice: `streamer` announced in `channel`, pinging @everyone."""
    return {
        "channel": {"value": channel, "title": "Canal de Texto", "style": "channel"},
        "streamer": {"value": streamer, "title": "Streamer"},
        "notification_messages": {"value": [MESSAGE], "title": "Mensagens de Notificação",
                                  "style": "bullet"},
        "mention": dict(EVERYONE),
    }


ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "notifications": {
        "style": "composition",
        "values": [notification("100", "gaules"), notification("102", "cellbit")],
    },
}
PAUSED = {**ENABLED, "enabled": False}


def known_streamers(deps):
    """The streamers the fake Twitch finds."""
    deps.twitch.add_user("gaules", user_id="181077473")
    deps.twitch.add_user("cellbit", user_id="28579002")
    deps.twitch.add_user("shroud", user_id="37402112")


async def open_card(scenario_factory, deps, locale):
    """The setup opened and its intro confirmed: a notice's card on screen."""
    known_streamers(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    return scenario


async def pick_channel(scenario, name="general"):
    """The notice's channel picked on the card."""
    await scenario.select_option(name, target="channel")


async def submit_streamer(scenario, name):
    """The streamer section opened and `name` typed in its modal."""
    await scenario.click("customize:streamer")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: name})


async def submit_messages(scenario):
    """The messages section opened and one message typed."""
    await scenario.click("customize:messages")
    first = scenario.pending_modal_fields()[0]
    await scenario.submit_modal({first: "{streamer} entrou ao vivo: {stream_link}"})


def seed(deps):
    """The streamers known and the enabled document saved."""
    known_streamers(deps)
    seed_document(deps, FORM, ENABLED)


def seed_paused(deps):
    """The streamers known and the paused document saved."""
    known_streamers(deps)
    seed_document(deps, FORM, PAUSED)


async def two_panels(scenario_factory, deps, locale):
    """The two saved streamers subscribed, and two admins each with the panel open."""
    known_streamers(deps)
    seed_document(deps, FORM, ENABLED)
    for login in ("gaules", "cellbit"):
        deps.twitch.subscribe_to_stream_online_event(deps.twitch._users[login].id)
    return await common.two_panels(scenario_factory, FORM, locale)

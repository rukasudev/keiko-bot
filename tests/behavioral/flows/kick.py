"""Kick lives: the saved notices, the channels the fake Kick knows, and the card's
steps (the channel, the streamer, the messages)."""

from tests.behavioral.flows.common import GUILD_ID, seed_document

FORM = "notifications_kick"

MESSAGE = "{streamer} está ao vivo! {stream_link}"
EVERYONE = {"value": "@everyone", "title": "Mencionar", "_raw_value": "everyone"}
BROADCASTERS = {"xqcow": 676, "adinross": 4810, "amouranth": 2525}


def notification(channel: str, streamer: str) -> dict:
    """One saved notice: the lives of `streamer` announced in `channel`, pinging
    @everyone."""
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
        "values": [notification("100", "xqcow")],
    },
}
TWO_ENTRIES = {
    **ENABLED,
    "notifications": {
        "style": "composition",
        "values": [notification("100", "xqcow"), notification("102", "adinross")],
    },
}
PAUSED = {**ENABLED, "enabled": False}


def known_streamers(deps):
    """The channels the fake Kick finds."""
    for slug, broadcaster_user_id in BROADCASTERS.items():
        deps.kick.add_channel(slug, broadcaster_user_id)


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
    await scenario.submit_modal({first: "{streamer} entrou ao vivo na Kick: {stream_link}"})


def seed(deps):
    """The channels known and the enabled document saved."""
    known_streamers(deps)
    seed_document(deps, FORM, ENABLED)


def seed_paused(deps):
    """The channels known and the paused document saved."""
    known_streamers(deps)
    seed_document(deps, FORM, PAUSED)


def seed_two_entries(deps):
    """The channels known and a document with two notices saved."""
    known_streamers(deps)
    seed_document(deps, FORM, TWO_ENTRIES)

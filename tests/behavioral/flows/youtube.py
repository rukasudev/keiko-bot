"""YouTube notifications: the saved notices, the channels the fake YouTube knows, and
the card's steps (the channel, the youtuber, the messages)."""

from tests.behavioral.flows.common import GUILD_ID, seed_document

FORM = "notifications_youtube_video"

MESSAGE = "{youtuber} acabou de postar um novo vídeo!"
EVERYONE = {"value": "@everyone", "title": "Mencionar", "_raw_value": "everyone"}


def notification(channel: str, youtuber: str) -> dict:
    """One saved notice: `youtuber` announced in `channel`, pinging @everyone."""
    return {
        "channel": {"value": channel, "title": "Canal de Texto", "style": "channel"},
        "youtuber": {"value": youtuber, "title": "Youtuber"},
        "notification_messages": {"value": [MESSAGE], "title": "Mensagens de Notificação",
                                  "style": "bullet"},
        "mention": dict(EVERYONE),
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


def known_channels(deps):
    """The channels the fake YouTube finds."""
    deps.youtube.add_channel("UC-lHJZR3Gqxm24_Vd_AJ5Yw", "PewDiePie", custom_url="@pewdiepie")
    deps.youtube.add_channel("UCX6OQ3DkcsbYNE6H8uQQuVA", "MrBeast", custom_url="@mrbeast")


async def open_card(scenario_factory, deps, locale):
    """The setup opened and its intro confirmed: a notice's card on screen."""
    known_channels(deps)
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    return scenario


async def pick_channel(scenario, name="general"):
    """The notice's channel picked on the card."""
    await scenario.select_option(name, target="channel")


async def submit_youtuber(scenario, name):
    """The youtuber section opened and `name` typed in its modal."""
    await scenario.click("customize:youtuber")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: name})


async def submit_messages(scenario):
    """The messages section opened and one message typed."""
    await scenario.click("customize:messages")
    first = scenario.pending_modal_fields()[0]
    await scenario.submit_modal({first: "{youtuber} postou: {video_link}"})


def seed(deps):
    """The channels known and the enabled document saved."""
    known_channels(deps)
    seed_document(deps, FORM, ENABLED)


def seed_paused(deps):
    """The channels known and the paused document saved."""
    known_channels(deps)
    seed_document(deps, FORM, PAUSED)


def seed_two_entries(deps):
    """The channels known and a document with two notices saved."""
    known_channels(deps)
    seed_document(deps, FORM, TWO_ENTRIES)

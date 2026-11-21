"""StreamElements commands: its saved document, the streamers the fake Twitch knows,
the streamer typed in its modal, and a chat command asked in a server's channel."""

from tests.behavioral.flows.common import GUILD_ID, seed_document
from tests.generators.moderations import recorded_server
from tests.mocks.database import fake_store
from tests.mocks.discord import create_guild, create_member, create_message

FORM = "stream_elements_commands"

ENABLED = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "streamer": "shroud",
    "channel_id": "5f1a2b3c4d5e6f7a8b9c0d1e",
}
PAUSED = {**ENABLED, "enabled": False}

DEFAULT_AVATAR = "https://cdn.discordapp.com/embed/avatars/0.png"


def known_streamers(deps):
    """The streamers the fake Twitch finds."""
    deps.twitch.add_user("shroud", user_id="37402112")
    deps.twitch.add_user("gaules", user_id="181077473")


async def submit_streamer(scenario, name):
    """`name` typed in the pending streamer modal and submitted."""
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: name})


def seed(deps):
    """The enabled document saved."""
    seed_document(deps, FORM, ENABLED)


def seed_paused(deps):
    """The paused document saved."""
    seed_document(deps, FORM, PAUSED)


def member_without_a_picture(guild):
    """TestUser (111), who never set a picture: Discord draws the default one."""
    member = create_member(guild, id=111, name="TestUser")
    member.avatar = None
    member._avatar_url = DEFAULT_AVATAR
    return member


async def ask(bot, guild, author, content="ks!mouse"):
    """`content` sent in the server's first channel, through the message listener."""
    from app.core import events as events_module

    message = create_message(content=content, author=author, channel=guild.text_channels[0])
    await events_module.Events(bot).on_message(message)
    return message


def english_guild():
    """A server recorded in en-us."""
    guild = create_guild()
    recorded_server(fake_store(), guild.id, language="en-us")
    return guild

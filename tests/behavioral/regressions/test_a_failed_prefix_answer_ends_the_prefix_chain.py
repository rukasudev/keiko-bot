"""A message on the server's prefix is answered once at most, even when an answer fails.

Broke as: `dispatch.message_sent` (`app/core/dispatch.py`) ran each prefix hook as
its own part and stopped the chain only when a hook returned True. A failed part
returns no value, so when Keiko's own reply to `ke!ping` raised (50013 Missing
Permissions in a read-only channel) StreamElements was asked next and answered the
same `!ping` with the streamer's command. When reading the server's prefix failed,
the prefix hooks were skipped but help's hint still ran and read the prefix again,
so a database blip that recovered sent the hint for a prefixed message. Before the
registry, Keiko's reply, StreamElements and the hint were one part, and a failure
ended it.

Shared behaviour: `dispatch.message_sent` and the `prefix_handled` it hands the
message hooks, read by help's `send_prefix_hint`. Consumers that exposed it:
help's prefix `ping` and stream_elements_commands.

Guaranteed: a prefix hook that fails ends the prefix chain, so no later hook answers
the same message; a prefix that could not be read skips the prefix hooks and the
hint; every hook still runs as its own part, and the message hooks (block links)
still run.
"""

import discord
import pytest

from app.integrations.stream_elements import StreamElementsClient
from app.core.utils import parse_locale
from tests.generators.moderations import recorded_server
from tests.mocks.database import fake_store
from tests.mocks.discord import create_guild, create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


@pytest.fixture
def streamer_has_ping(mock_cache, monkeypatch):
    """A server where StreamElements is on for a streamer with a `!ping` command."""

    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {"streamer": "gaules", "state": {"channel_id": "se-gaules"}, "enabled": True}
        return {}

    mock_cache.side_effect = configured
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(
            lambda channel_id: [{"command": "ping", "reply": "Pong do streamer", "enabled": True}]
        ),
    )


@pytest.fixture
def nothing_on(mock_cache):
    """A server with no feature on."""
    mock_cache.return_value = {}
    return mock_cache


def server_message(content):
    guild = create_guild()
    recorded_server(fake_store(), guild.id, language=parse_locale("en-us"), prefix="ke!")
    author = create_member(guild, id=111, name="TestUser")
    return create_message(content=content, author=author, channel=guild.text_channels[0])


async def on_message(bot, message):
    from app.core.events import Events

    await Events(bot).on_message(message)


def refused():
    return discord.Forbidden(
        type("Response", (), {"status": 403, "reason": "Forbidden"})(),
        {"code": 50013, "message": "Missing Permissions"},
    )


async def test_a_failed_own_reply_is_not_answered_by_stream_elements(
    deps, streamer_has_ping
):
    deps.bot.config.PREFIX = "ke!"
    deps.bot.latency = 0.0421
    message = server_message("ke!ping")
    message._reply.side_effect = [refused(), None]

    await on_message(deps.bot, message)

    assert message._reply.await_count == 1, "only Keiko's own reply was tried"
    assert message.channel._sent_messages == []


async def test_a_prefix_that_could_not_be_read_sends_no_hint(
    deps, nothing_on, monkeypatch
):
    from app.core import events as events_module
    from app.exceptions import ServerSettingsMissing

    async def unreadable(guild_id):
        raise ServerSettingsMissing(guild_id)

    deps.bot.config.PREFIX = "ke!"
    message = server_message("ke!mouse")
    monkeypatch.setattr(events_module, "server_prefix", unreadable)

    await on_message(deps.bot, message)

    assert message.channel._sent_messages == [], "the hint waits for a prefix it can read"
    assert message._reply.await_count == 0


async def test_a_readable_prefix_still_gets_the_hint(deps, nothing_on):
    deps.bot.config.PREFIX = "ke!"
    message = server_message("ke!mouse")

    await on_message(deps.bot, message)

    assert len(message.channel._sent_messages) == 1

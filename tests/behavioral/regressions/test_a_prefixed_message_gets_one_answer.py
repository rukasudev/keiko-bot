"""A message with the prefix gets one answer: the feature's, or Keiko's hint.

Broke as: discord.py hands every message that starts with the configured prefix
(`ke!` in production) and names no text command to `Errors.on_command_error`,
which always answered with a bilingual "use slashes" hint written in Python.
While the StreamElements listener answered a fixed `ks!`, the two never met; with
one prefix for both, every StreamElements command would have been answered twice,
by the reply and by the hint. The hint never said how to turn a prefix feature on.

Shared behaviour: `Errors.on_command_error` (`app/cogs/errors.py`), the
`answers_prefix` mark on `Commands.SETUP_FEATURES` and
`app/services/prefix_features.py`, beside `Events.on_message`. Consumer that
exposed it: stream_elements_commands, the first feature that answers the prefix.

Guaranteed: in a server where a prefix feature is on, a prefixed message gets the
feature's answer and nothing from Keiko; in a server with none on, it gets one
message in the server's language naming the configured prefix, the command that
turns each prefix feature on, and `/help` and `/setup` by their localized names;
a direct message gets the slash hint, from the language files.
"""
from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

from app.integrations.stream_elements import StreamElementsClient
from app.services.utils import ml
from tests.mocks.discord import (
    MockChannel,
    MockMessage,
    create_guild,
    create_member,
    create_message,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

NOT_A_COMMAND = commands.CommandNotFound('Command "mouse" is not found')


@pytest.fixture
def stream_elements_on(mock_cache, monkeypatch):
    """A server where StreamElements commands are on, for `gaules` and his `!mouse`."""

    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {"streamer": "gaules", "channel_id": "se-gaules", "enabled": True}
        return {}

    mock_cache.side_effect = configured
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(
            lambda channel_id: [{"command": "mouse", "reply": "A Logitech!", "enabled": True}]
        ),
    )


@pytest.fixture
def nothing_on(mock_cache):
    """A server with no feature on."""
    mock_cache.return_value = {}
    return mock_cache


def server_message(content, locale=discord.Locale.american_english):
    guild = create_guild()
    guild.preferred_locale = locale
    author = create_member(guild, id=111, name="TestUser")
    return create_message(content=content, author=author, channel=guild.text_channels[0])


def direct_message(content):
    channel = MockChannel(id=55, name="dm")
    return MockMessage(id=1, content=content, author=create_member(create_guild()), channel=channel)


async def look_up_the_command(bot, message):
    """What discord.py does when no text command matches the prefixed message."""
    from app.cogs.errors import Errors

    context = SimpleNamespace(guild=message.guild, message=message, send=message.channel.send)
    await Errors(bot).on_command_error(context, NOT_A_COMMAND)


async def handle(bot, message):
    """Everything discord.py does with a server message: the listeners, then the lookup."""
    from app.cogs.events import Events

    await Events(bot).on_message(message)
    await look_up_the_command(bot, message)


def sent(message):
    return [answer.content for answer in message.channel._sent_messages]


def answers(message):
    return ["the StreamElements reply"] * message._reply.await_count + sent(message)


async def test_a_stream_elements_command_gets_only_its_reply(deps, stream_elements_on):
    deps.bot.config.PREFIX = "ke!"
    message = server_message("ke!mouse")

    await handle(deps.bot, message)

    assert answers(message) == ["the StreamElements reply"]


@pytest.mark.parametrize(
    "locale, opener, activation, general",
    [
        (
            discord.Locale.brazil_portuguese,
            "🐾 Au au!",
            "`/integrações comandos streamelements`",
            ("`/ajuda`", "`/configurar`"),
        ),
        (
            discord.Locale.american_english,
            "🐾 Woof!",
            "`/integrations streamelements commands`",
            ("`/help`", "`/setup`"),
        ),
    ],
)
async def test_a_prefixed_message_no_feature_answers_gets_the_hint_in_the_server_language(
    deps, nothing_on, locale, opener, activation, general
):
    deps.bot.config.PREFIX = "ke!"
    message = server_message("ke!mouse", locale)

    await handle(deps.bot, message)

    [hint] = sent(message)
    assert hint.startswith(opener)
    assert "**ke!**" in hint
    assert f"\n- {activation}\n" in hint, "one line per feature that answers the prefix"
    assert all(name in hint for name in general)
    assert message._reply.await_count == 0


async def test_the_hint_names_the_configured_prefix(deps, nothing_on):
    deps.bot.config.PREFIX = "kk!"
    message = server_message("kk!mouse")

    await handle(deps.bot, message)

    [hint] = sent(message)
    assert "**kk!**" in hint
    assert "ke!" not in hint and "ks!" not in hint


async def test_a_direct_message_gets_the_slash_hint_from_the_language_files(
    deps, mock_cache
):
    deps.bot.config.PREFIX = "ke!"
    message = direct_message("ke!mouse")

    await look_up_the_command(deps.bot, message)

    halves = [ml("messages.prefix-hint.direct", locale) for locale in ("pt-br", "en-us")]
    assert "messages.prefix-hint.direct" not in halves, "the hint lives in the language files"
    [hint] = sent(message)
    assert hint == "\n\n".join(halves)
    assert "`/ajuda`" in hint and "`/help`" in hint
    assert mock_cache.call_count == 0, "a direct message has no server to read"

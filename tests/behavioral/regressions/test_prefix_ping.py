"""A prefix command `ping` answers on the server's prefix, in the server's language.

Lucas (PR15 round 2, e12.4): after changing the prefix on /configurar there was
no way to see that Keiko heard the new one, since only StreamElements answers a
prefix and only once it is set up. `ping` is one of Keiko's own prefix commands,
answered by the same listener path (`Events.on_message`) before any feature
reads the message. Round 3 (item 6): it answers as the slash `/ping` does, the
same "🏓 Pong!" embed with Keiko's gateway latency (`bot.latency`) in ms.

Guaranteed: `<prefix>ping` gets one reply in the server's saved language with
the /ping embed and the latency, whatever the asker's Discord language; the
same word after another prefix gets no reply; a longer word that starts with
ping is not ping; the slash /ping answers with the same embed.
"""
import pytest

from app.data.guild import settings as guild_settings
from tests.generators.moderations import recorded_server
from tests.mocks.discord import create_guild, create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


async def say(bot, guild, content):
    from app.cogs import events as events_module

    author = create_member(guild, id=111, name="TestUser")
    message = create_message(content=content, author=author, channel=guild.text_channels[0])
    await events_module.Events(bot).on_message(message)
    return message


def server(language, prefix):
    guild = create_guild()
    recorded_server(guild_settings.mongo_client, guild.id, language=language, prefix=prefix)
    return guild


@pytest.mark.parametrize("language,description", [
    ("pt-br", "Minha latência atual é de **42 ms** 🐾"),
    ("en-us", "My current latency is **42 ms** 🐾"),
])
async def test_ping_answers_pong_and_the_latency_in_the_servers_language(
    deps, language, description
):
    deps.bot.latency = 0.0421
    guild = server(language, "ke?")

    message = await say(deps.bot, guild, "ke?ping")

    assert message._reply.await_count == 1
    embed = message._reply.await_args.kwargs["embed"]
    assert (embed.title, embed.description) == ("🏓 Pong!", description)


async def test_the_slash_ping_answers_with_the_same_embed(deps):
    from unittest.mock import AsyncMock, MagicMock

    from app.cogs.base.ping import Ping

    deps.bot.latency = 0.0421
    interaction = MagicMock()
    interaction.locale = "pt-br"
    interaction.response.send_message = AsyncMock()

    await Ping.ping.callback(Ping(deps.bot), interaction)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert (embed.title, embed.description) == (
        "🏓 Pong!", "Minha latência atual é de **42 ms** 🐾"
    )


async def test_ping_after_another_prefix_gets_no_reply(deps):
    guild = server("pt-br", "ke?")

    message = await say(deps.bot, guild, "kb!ping")

    assert message._reply.await_count == 0
    assert guild.text_channels[0]._send.await_count == 0


async def test_a_word_that_only_starts_with_ping_is_not_ping(deps):
    guild = server("pt-br", "ke?")

    message = await say(deps.bot, guild, "ke?pingado")

    assert message._reply.await_count == 0

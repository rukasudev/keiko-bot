"""A StreamElements command gets its reply, and every copy names the configured prefix.

Broke in PR #40: `create_response_embed` read `bot.config.PREFIX` in a module
that never imports `bot`, and `user.avatar.url` for a member who never set a
picture, whose `avatar` is None. Every `ks!` command raised instead of being
answered. The footer was English only and named the configured prefix, `ke!` in
production, while the listener only answered a fixed `ks!`; the setup form and
the review's "My version" field named the same `ke!`. Two sources for one value
were the bug, so the listener reads the configured prefix too.

Shared behaviour: `Events.on_message`, `stream_elements.check_message`, the
`{context:prefix}` token of the form copy, which the adapter fills from the
configuration, and the StreamElements lookup, which reads `OpenContext.prefix`.
Consumer that exposed it: stream_elements_commands (`ruff --select F821` names
the line).

Guaranteed: a command with the configured prefix (`ke!` in production) is
answered with the picture of whoever asked, default or not, and a footer in the
server's language; no other prefix is answered; the reply, the setup form and the
review's "My version" all name the configured prefix, whatever it is.
"""
from types import SimpleNamespace

import discord
import pytest

from app.integrations.stream_elements import StreamElementsClient
from app.services.utils import ml
from tests.behavioral.harness.driver import FormScenario
from tests.mocks.discord import create_guild, create_member, create_message
from app.services import stream_elements

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

DEFAULT_AVATAR = "https://cdn.discordapp.com/embed/avatars/0.png"
OWN_AVATAR = "https://cdn.discordapp.com/avatars/111/a1b2c3.png"
REPLY = "The streamer uses a Logitech G Pro!"


@pytest.fixture
def streamer_commands(mock_cache, monkeypatch):
    """A server that set up StreamElements for `gaules`, who has a `!mouse` command."""

    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {"streamer": "gaules", "channel_id": "se-gaules", "enabled": True}
        return {}

    mock_cache.side_effect = configured
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(
            lambda channel_id: [{"command": "mouse", "reply": REPLY, "enabled": True}]
        ),
    )


def member_without_a_picture(guild):
    member = create_member(guild, id=111, name="TestUser")
    member.avatar = None
    member._avatar_url = DEFAULT_AVATAR
    return member


async def ask(bot, guild, author, content="ks!mouse"):
    from app.cogs import events as events_module

    message = create_message(content=content, author=author, channel=guild.text_channels[0])
    await events_module.Events(bot).on_message(message)
    return message


def reply_of(message):
    return message._reply.await_args.kwargs["embed"]


def english_guild():
    guild = create_guild()
    guild.preferred_locale = discord.Locale.american_english
    return guild


async def test_a_member_with_the_default_avatar_gets_the_reply(deps, streamer_commands):
    guild = english_guild()

    message = await ask(deps.bot, guild, member_without_a_picture(guild))

    embed = reply_of(message)
    assert embed.author.name == "!mouse"
    assert embed.author.icon_url == DEFAULT_AVATAR
    assert embed.description == REPLY
    assert embed.footer.text.startswith("• ")
    assert "ks!commands" in embed.footer.text
    assert "gaules" in embed.footer.text


async def test_a_member_with_a_picture_of_their_own_gets_the_reply(deps, streamer_commands):
    guild = english_guild()
    member = create_member(guild, id=111, name="TestUser")
    member.avatar = SimpleNamespace(url=OWN_AVATAR)
    member._avatar_url = OWN_AVATAR

    message = await ask(deps.bot, guild, member)

    assert reply_of(message).author.icon_url == OWN_AVATAR


async def test_the_reply_footer_speaks_the_language_of_the_server(deps, streamer_commands):
    guild = create_guild()
    guild.preferred_locale = discord.Locale.brazil_portuguese

    message = await ask(deps.bot, guild, member_without_a_picture(guild))

    footer = ml(
        "commands.commands.commons.stream-elements-manager.commands.reply-footer",
        "pt-br",
    )
    expected = footer.replace("$command", "ks!commands").replace("$streamer", "gaules")
    assert reply_of(message).footer.text == f"• {expected}"
    assert expected != ml(
        "commands.commands.commons.stream-elements-manager.commands.reply-footer",
        "en-us",
    ).replace("$command", "ks!commands").replace("$streamer", "gaules")


async def test_the_listener_answers_the_configured_prefix_and_no_other(
    deps, streamer_commands
):
    deps.bot.config.PREFIX = "kk!"
    guild = english_guild()

    answered = await ask(deps.bot, guild, member_without_a_picture(guild), "kk!mouse")
    ignored = await ask(deps.bot, guild, member_without_a_picture(guild), "ks!mouse")

    assert answered._reply.await_count == 1, "the listener answers the configured prefix"
    assert "kk!commands" in reply_of(answered).footer.text
    assert ignored._reply.await_count == 0


async def test_the_setup_names_the_configured_prefix(deps):
    deps.bot.config.PREFIX = "kk!"
    guild = create_guild()
    scenario = FormScenario(
        guild=guild,
        user=create_member(guild, id=555, name="Tester"),
        locale="en-us",
        mongo=deps.mongo_client,
    )

    await scenario.start("stream_elements_commands")

    intro = scenario.current_message.embeds[0].description
    assert "**kk!**" in intro and "`kk!mouse`" in intro
    assert "ks!" not in intro


async def test_my_version_names_the_configured_prefix(deps, monkeypatch):
    deps.bot.config.PREFIX = "kk!"
    deps.twitch.add_user("gaules", user_id="181077473")
    monkeypatch.setattr(
        StreamElementsClient, "get_channel_info", staticmethod(lambda name: {"_id": "se1"})
    )
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(lambda channel_id: [{"enabled": True, "command": "mouse"}]),
    )
    guild = create_guild()
    scenario = FormScenario(
        guild=guild,
        user=create_member(guild, id=555, name="Tester"),
        locale="en-us",
        mongo=deps.mongo_client,
    )

    await scenario.start("stream_elements_commands")
    await scenario.confirm()
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "gaules"})

    review = scenario.transcript
    assert "`kk!mouse`" in review, "My version names the configured prefix"
    assert "ks!" not in review


REPLY_IN_THE_ASKERS_WORDS = "$(user) uses a Logitech G Pro!"


@pytest.fixture
def counted_streamer_commands(mock_cache, monkeypatch):
    """A server that set up StreamElements for `gaules`, who has a `!mouse` command."""
    fetched = []

    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {"streamer": "gaules", "channel_id": "se-gaules", "enabled": True}
        return {}

    def commands(channel_id):
        fetched.append(channel_id)
        return [
            {"command": "mouse", "reply": REPLY_IN_THE_ASKERS_WORDS, "enabled": True},
            {"command": "old", "reply": "gone", "enabled": False},
        ]

    mock_cache.side_effect = configured
    monkeypatch.setattr(StreamElementsClient, "get_chat_commands", staticmethod(commands))
    return fetched


async def test_a_second_command_is_answered_from_the_cache_in_its_askers_words(
    deps, counted_streamer_commands
):
    """A StreamElements command is answered from the day's cache, in the asker's own words.

    Broke as: `get_reply_in_cache_or_populate` looked the typed command up (`mouse`)
    in a cache it had filled under another name (`!mouse`), so it never hit: every
    message with the prefix called StreamElements, a command the streamer does not
    have included, in every server, and the one-day cache was written and never
    read. The cached replies had also been filled in for the first asker (their
    mention in `$(user)`), so a cache that hit would have answered everyone with
    the first asker's name. Then (review round 1): a streamer with no command was
    never cached, so each of their prefixed messages still called StreamElements,
    and the error context of a failed reply copied the member's typed command.

    Shared behaviour: the StreamElements command cache (`check_message`, the
    `!commands` list and the manager panel's count). Consumer that exposed it:
    stream_elements_commands (the review of v1, the Redis table of section 12).

    Guaranteed: the streamer's commands are fetched once a day per StreamElements
    channel, and a streamer with none once in a while; a failure is never cached; a
    command, known or not, is answered from that cache; each asker's placeholders
    are filled when the reply is sent; the list keeps showing `!name` with the
    asker's words; an error names how long the typed command was, never its text;
    and v0.9.0 keeps the cache key it reads.
    """
    guild = english_guild()
    first = create_member(guild, id=111, name="First")
    second = create_member(guild, id=222, name="Second")

    answered_first = await ask(deps.bot, guild, first, "ks!mouse")
    answered_second = await ask(deps.bot, guild, second, "ks!mouse")

    assert counted_streamer_commands == ["se-gaules"], (
        f"StreamElements was called {len(counted_streamer_commands)} times for two commands"
    )
    first_reply = answered_first._reply.await_args.kwargs["embed"].description
    second_reply = answered_second._reply.await_args.kwargs["embed"].description
    assert first_reply == "<@111> uses a Logitech G Pro!"
    assert second_reply == "<@222> uses a Logitech G Pro!", (
        "the cached reply kept the first asker's mention"
    )


async def test_a_command_the_streamer_does_not_have_costs_no_call(
    deps, counted_streamer_commands
):
    guild = english_guild()
    member = create_member(guild, id=111, name="First")

    await ask(deps.bot, guild, member, "ks!mouse")
    unknown = await ask(deps.bot, guild, member, "ks!keyboard")
    disabled = await ask(deps.bot, guild, member, "ks!old")

    assert counted_streamer_commands == ["se-gaules"]
    assert unknown._reply.await_count == 0
    assert disabled._reply.await_count == 0


def test_the_command_list_keeps_its_names_and_the_askers_words(deps, counted_streamer_commands):
    guild = english_guild()
    first = create_member(guild, id=111, name="First")
    second = create_member(guild, id=222, name="Second")

    stream_elements.get_reply_in_cache_or_populate("se-gaules", "mouse", first)
    listed = stream_elements.get_commands_in_cache_or_populate("se-gaules", second)

    assert listed == {"!mouse": "<@222> uses a Logitech G Pro!"}
    assert counted_streamer_commands == ["se-gaules"]


def test_v090_keeps_the_cache_key_it_reads(deps, counted_streamer_commands):
    """v0.9.0 reads `streamelements:commands:{channel}` and would answer a raw reply."""
    member = create_member(english_guild(), id=111, name="First")

    stream_elements.get_reply_in_cache_or_populate("se-gaules", "mouse", member)

    assert deps.redis_client.get("streamelements:commands:se-gaules") is None


def test_a_streamer_without_commands_is_asked_once_in_a_while(deps, monkeypatch):
    fetched = []
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(lambda channel_id: fetched.append(channel_id) or []),
    )
    member = create_member(english_guild(), id=111, name="First")

    first = stream_elements.get_reply_in_cache_or_populate("se-quiet", "mouse", member)
    second = stream_elements.get_reply_in_cache_or_populate("se-quiet", "mouse", member)

    assert (first, second) == (None, None)
    assert fetched == ["se-quiet"], "a streamer with no command was asked on every message"
    assert deps.redis_client.ttl("stream_elements:channel:se-quiet:commands") <= 60 * 5


@pytest.mark.parametrize(
    "failure", [ConnectionError("StreamElements is down"), {"statusCode": 404}]
)
def test_a_stream_elements_failure_is_never_cached(deps, monkeypatch, failure):
    answers = [failure, [{"command": "mouse", "reply": REPLY_IN_THE_ASKERS_WORDS, "enabled": True}]]

    def commands(channel_id):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(StreamElementsClient, "get_chat_commands", staticmethod(commands))
    member = create_member(english_guild(), id=111, name="First")

    try:
        stream_elements.get_reply_in_cache_or_populate("se-gaules", "mouse", member)
    except ConnectionError:
        pass
    reply = stream_elements.get_reply_in_cache_or_populate("se-gaules", "mouse", member)

    assert reply == "<@111> uses a Logitech G Pro!", "the failure was cached as no command"
    assert answers == []


async def test_an_error_names_the_commands_length_never_its_text(
    deps, mock_cache, monkeypatch
):
    def configured(guild_id, key, *args, **kwargs):
        return {"streamer": "gaules", "channel_id": "se-gaules", "enabled": True}

    def down(channel_id):
        raise ConnectionError("StreamElements is down")

    mock_cache.side_effect = configured
    monkeypatch.setattr(StreamElementsClient, "get_chat_commands", staticmethod(down))
    logged = []
    monkeypatch.setattr(
        stream_elements.logger, "error", lambda *args, **kwargs: logged.append(kwargs)
    )
    guild = english_guild()
    message = create_message(
        content="ks!my-token-s3cr3t", author=create_member(guild, id=111, name="First"),
        channel=guild.text_channels[0],
    )

    with pytest.raises(ConnectionError):
        await stream_elements.check_message(str(guild.id), message, "ks!")

    extra = logged[0]["context"].extra
    assert "command" not in extra
    assert extra["command_length"] == len("my-token-s3cr3t")
    copied = {
        key: value for key, value in extra.items()
        if key != "message_preview" and "s3cr3t" in str(value)
    }
    assert copied == {}, f"the error context copied what the member typed: {copied}"

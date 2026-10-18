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
from tests.mocks.discord import (
    create_guild,
    create_member,
    create_message,
    MockChannel,
    MockMessage,
)
from app.services import stream_elements
from tests.behavioral.harness.locators import walk_items
from discord.ext import commands

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


COUNTED = "Comandos ativos"


def _texts(message):
    texts = [embed.description or "" for embed in message.embeds or []]
    if message.view is not None:
        texts += [
            item.content
            for item in walk_items(message.view)
            if isinstance(item, discord.ui.TextDisplay)
        ]
    return "\n".join(texts)


async def _review_after_naming(scenario_factory, deps):
    deps.twitch.add_user("shroud", user_id="37402112")
    scenario = await scenario_factory(locale="pt-br").start("stream_elements_commands")
    await scenario.confirm()
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "shroud"})
    return scenario


async def test_an_unreachable_stream_elements_leaves_no_count_on_the_review(
    scenario_factory, deps, monkeypatch
):
    """The review says what it knows, and says when it knows nothing.

    The StreamElements review counts the streamer's active commands with a
    lookup. When StreamElements cannot be reached the count is absent, and the
    review must leave the line out rather than claim a number it does not have.

    Shared behaviour: a review line over an answer a lookup produced
    (`lookup_answers`), which is dropped when the answer is absent. Consumer that
    exposed it: stream_elements_commands. Guaranteed: an unreachable service
    leaves no count on the screen, and a reachable one shows the count it found.
    """
    from app.settings.features import stream_elements_commands as feature

    def unreachable(name):
        raise ConnectionError("StreamElements is down")

    monkeypatch.setattr(
        feature.StreamElementsClient, "get_channel_info", staticmethod(unreachable)
    )

    scenario = await _review_after_naming(scenario_factory, deps)

    review = _texts(scenario.current_message)
    assert COUNTED not in review
    assert "shroud" in review
    await scenario.finish()


async def test_a_reachable_stream_elements_counts_the_commands_on_the_review(
    scenario_factory, deps
):
    scenario = await _review_after_naming(scenario_factory, deps)

    review = _texts(scenario.current_message)
    assert f"{COUNTED}:** 2" in review
    await scenario.finish()


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
    """A message with the prefix gets one answer: the feature's, or Keiko's hint.

    Broke as: discord.py hands every message that starts with the configured prefix
    (`ke!` in production) and names no text command to `Errors.on_command_error`,
    which always answered with a bilingual "use slashes" hint written in Python.
    While the StreamElements listener answered a fixed `ks!`, the two never met; with
    one prefix for both, every StreamElements command would have been answered twice,
    by the reply and by the hint. The hint never said how to turn a prefix feature on.

    Shared behaviour: `Errors.on_command_error` (`app/cogs/errors.py`), the
    `answers_prefix` mark on `Commands.SETUP_FEATURES` and
    `prefix_hint` (`app/services/help.py`), beside `Events.on_message`. Consumer that
    exposed it: stream_elements_commands, the first feature that answers the prefix.

    Guaranteed: in a server where a prefix feature is on, a prefixed message gets the
    feature's answer and nothing from Keiko; in a server with none on, it gets one
    message in the server's language naming the configured prefix, the command that
    turns each prefix feature on, and `/help` and `/setup` by their localized names;
    a direct message gets the slash hint, from the language files.
    """
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

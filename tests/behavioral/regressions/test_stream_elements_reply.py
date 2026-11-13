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
server's settings, and the StreamElements lookup, which reads `OpenContext.prefix`.
Consumer that exposed it: stream_elements_commands (`ruff --select F821` names
the line).

Guaranteed: a command with the server's prefix (the configured `ke!` until its
admin saves another, PR15) is answered with the picture of whoever asked, default
or not, and a footer in the server's language; no other prefix is answered; the
reply, the setup form and the review's "My version" all name the server's prefix,
whatever it is.
"""
from types import SimpleNamespace

import discord
import pytest

from app.data.guild import settings as guild_settings
from app.exceptions import StreamElementsUnavailable
from app.integrations.stream_elements import StreamElementsClient
from app.core.utils import ml, parse_locale
from tests.behavioral.harness.driver import FormScenario
from app.data.guild.cogs import find_cog_by_guild_id
from tests.generators.moderations import recorded_server, seed_cog
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
            return {"streamer": "gaules", "state": {"channel_id": "se-gaules"}, "enabled": True}
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
    recorded_server(guild_settings.mongo_client, guild.id, language="en-us")
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
    recorded_server(guild_settings.mongo_client, guild.id, language="pt-br")

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


async def test_the_listener_answers_the_servers_prefix_and_no_other(
    deps, streamer_commands
):
    deps.bot.config.PREFIX = "ke!"
    guild = english_guild()
    recorded_server(deps.mongo_client, guild.id, prefix="kk!")

    answered = await ask(deps.bot, guild, member_without_a_picture(guild), "kk!mouse")
    ignored = await ask(deps.bot, guild, member_without_a_picture(guild), "ks!mouse")
    default = await ask(deps.bot, guild, member_without_a_picture(guild), "ke!mouse")

    assert answered._reply.await_count == 1, "the listener answers the server's prefix"
    assert "kk!commands" in reply_of(answered).footer.text
    assert ignored._reply.await_count == 0
    assert default._reply.await_count == 0, "the default prefix no longer answers there"


async def test_the_setup_names_the_servers_prefix(deps):
    deps.bot.config.PREFIX = "ke!"
    guild = create_guild()
    recorded_server(deps.mongo_client, guild.id, prefix="kk!")
    scenario = FormScenario(
        guild=guild,
        user=create_member(guild, id=555, name="Tester"),
        locale="en-us",
        mongo=deps.mongo_client,
    )

    await scenario.start("stream_elements_commands")

    intro = scenario.current_message.embeds[0].description
    assert "**kk!**" in intro and "`kk!mouse`" in intro
    assert "ks!" not in intro and "ke!" not in intro


async def test_my_version_names_the_servers_prefix(deps, monkeypatch):
    deps.bot.config.PREFIX = "ke!"
    recorded_server(deps.mongo_client, create_guild().id, prefix="kk!")
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
    assert "`kk!mouse`" in review, "My version names the server's prefix"
    assert "ks!" not in review and "ke!" not in review


REPLY_IN_THE_ASKERS_WORDS = "$(user) uses a Logitech G Pro!"


@pytest.fixture
def counted_streamer_commands(mock_cache, monkeypatch):
    """A server that set up StreamElements for `gaules`, who has a `!mouse` command."""
    fetched = []

    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {"streamer": "gaules", "state": {"channel_id": "se-gaules"}, "enabled": True}
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

    with pytest.raises(StreamElementsUnavailable):
        stream_elements.get_reply_in_cache_or_populate("se-gaules", "mouse", member)
    reply = stream_elements.get_reply_in_cache_or_populate("se-gaules", "mouse", member)

    assert reply == "<@111> uses a Logitech G Pro!", "the failure was cached as no command"
    assert answers == []


async def test_an_error_names_the_commands_length_never_its_text(
    deps, mock_cache, monkeypatch
):
    def configured(guild_id, key, *args, **kwargs):
        return {"streamer": "gaules", "state": {"channel_id": "se-gaules"}, "enabled": True}

    def unreadable(reply, user):
        raise ValueError("the reply could not be read")

    mock_cache.side_effect = configured
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(
            lambda channel_id: [
                {"command": "my-token-s3cr3t", "reply": "a reply", "enabled": True}
            ]
        ),
    )
    monkeypatch.setattr(stream_elements, "parse_placeholders", unreadable)
    logged = []
    monkeypatch.setattr(
        stream_elements.logger, "error", lambda *args, **kwargs: logged.append(kwargs)
    )
    guild = english_guild()
    message = create_message(
        content="ks!my-token-s3cr3t", author=create_member(guild, id=111, name="First"),
        channel=guild.text_channels[0],
    )

    with pytest.raises(ValueError):
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
            return {"streamer": "gaules", "state": {"channel_id": "se-gaules"}, "enabled": True}
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
    recorded_server(guild_settings.mongo_client, guild.id, language=parse_locale(locale))
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

    Shared behaviour: `Events._answer_the_prefix` (`app/cogs/events.py`), which
    decides every prefixed message since PR15 round 3, the `answers_prefix` mark on
    `Commands.SETUP_FEATURES` and `prefix_hint` (`app/services/help.py`). Consumer
    that exposed it: stream_elements_commands, the first feature that answers the
    prefix.

    Guaranteed: in a server where a prefix feature is on, a prefixed message gets the
    feature's answer and nothing from Keiko; in a server with none on, it gets one
    message in the server's language naming the configured prefix, the command that
    turns each prefix feature on, and `/help` and `/setup` by their localized names;
    a direct message gets the slash hint, from the language files.
    """
    deps.bot.config.PREFIX = "ke!"
    message = server_message("ke!mouse")
    recorded_server(deps.mongo_client, message.guild.id, prefix="ke!")

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
    recorded_server(deps.mongo_client, message.guild.id, prefix="ke!")

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
    recorded_server(deps.mongo_client, message.guild.id, prefix="kk!")

    await handle(deps.bot, message)

    [hint] = sent(message)
    assert "**kk!**" in hint
    assert "ke!" not in hint and "ks!" not in hint


async def test_ping_on_the_default_prefix_gets_only_the_pong(deps, nothing_on):
    """A server still on Keiko's default prefix gets one answer to `ke!ping`.

    Broke as: `Events._answer_the_prefix` answered `ke!ping` with the Pong, then
    discord.py found no text command `ping` and handed the message to
    `Errors.on_command_error`, which answered every `CommandNotFound` that started
    with the default prefix with the prefix hint. A server whose prefix is the
    default, every new server, got the Pong and the hint.

    Shared behaviour: `Events._answer_the_prefix` (`app/cogs/events.py`), which now
    decides every prefixed message, and `Errors.on_command_error`, silent on
    `CommandNotFound`. Consumer that exposed it: the prefix `ping` (ux-493, ux-525).

    Guaranteed: on the default prefix, `ke!ping` gets the Pong and nothing else.
    """
    deps.bot.config.PREFIX = "ke!"
    deps.bot.latency = 0.0421
    message = server_message("ke!ping")
    recorded_server(deps.mongo_client, message.guild.id, prefix="ke!")

    await handle(deps.bot, message)

    assert message._reply.await_count == 1
    assert message._reply.await_args.kwargs["embed"].title == "🏓 Pong!"
    assert sent(message) == []


async def as_discord_reads(bot, message):
    """The listeners, then the lookup only for a message with the prefix the bot reads it with."""
    from app.bot import server_command_prefix
    from app.cogs.events import Events

    await Events(bot).on_message(message)
    if message.content.startswith(await server_command_prefix(bot, message)):
        await look_up_the_command(bot, message)


async def test_a_server_with_its_own_prefix_hints_only_at_the_default(deps, nothing_on):
    """PR15: a server saves its own prefix. The hint answers only Keiko's default
    prefix, once, and names the server's; a message with the server's own prefix
    that no feature answers gets nothing, since other bots share short prefixes."""
    deps.bot.config.PREFIX = "ke!"
    default = server_message("ke!mouse")
    own = server_message("!mouse")
    recorded_server(deps.mongo_client, default.guild.id, prefix="!")

    await as_discord_reads(deps.bot, default)
    await as_discord_reads(deps.bot, own)

    [hint] = sent(default)
    assert "**!**" in hint and "ke!" not in hint
    assert sent(own) == []


async def test_a_server_with_a_prefix_inside_the_default_still_hints_once(deps, nothing_on):
    deps.bot.config.PREFIX = "ke!"
    message = server_message("ke!mouse")
    recorded_server(deps.mongo_client, message.guild.id, prefix="ke")

    await as_discord_reads(deps.bot, message)

    assert len(sent(message)) == 1


async def test_a_direct_message_gets_the_slash_hint_from_the_language_files(
    deps, mock_cache
):
    deps.bot.config.PREFIX = "ke!"
    message = direct_message("ke!mouse")

    await handle(deps.bot, message)

    halves = [ml("messages.prefix-hint.direct", locale) for locale in ("pt-br", "en-us")]
    assert "messages.prefix-hint.direct" not in halves, "the hint lives in the language files"
    [hint] = sent(message)
    assert hint == "\n\n".join(halves)
    assert "`/ajuda`" in hint and "`/help`" in hint
    assert mock_cache.call_count == 0, "a direct message has no server to read"


async def test_a_setup_in_dev_saves_the_channel_the_streamer_lookup_found(
    scenario_factory, deps, monkeypatch
):
    """A dev setup saves the StreamElements channel the streamer lookup found.

    Broke as: `_with_channel` (`app/settings/features/stream_elements_commands.py`)
    returned the document untouched when `is_dev()`, so a setup on the dev bot saved
    no `channel_id`. The panel then asked StreamElements for `/bot/commands//public`
    and found no command, and every `ke!` command got no answer. The streamer modal's
    lookup had already found the channel and the commit threw it away.

    Shared behaviour: `lookup_answers` of the streamer modal, which keeps the
    channel the prefetch found as a hidden answer, and the feature's commit hooks.
    Consumer that exposed it: stream_elements_commands on the dev bot.

    Guaranteed: in every environment the saved document carries the channel id
    the streamer lookup found, without asking StreamElements for it again.
    """
    deps.bot.config.is_dev = lambda: True
    asked = []

    def channel_info(name):
        asked.append(name)
        return {"_id": f"se-channel-of-{name}"}

    monkeypatch.setattr(StreamElementsClient, "get_channel_info", staticmethod(channel_info))
    scenario = await _review_after_naming(scenario_factory, deps)
    await scenario.confirm()
    await scenario.finish()

    scenario.expect_persisted(
        "guild",
        "stream_elements_commands",
        {"guild_id": str(scenario.guild.id)},
        {"streamer": "shroud", "state": {"channel_id": "se-channel-of-shroud"}},
    )
    assert asked == ["shroud"], "the commit asked StreamElements again for the channel"


@pytest.fixture
def an_old_document(deps, mock_cache, monkeypatch):
    """A server whose document was saved by the dev bot: a streamer and no channel."""
    guild = english_guild()
    document = {"guild_id": str(guild.id), "streamer": "gaules", "enabled": True}
    seed_cog(deps.mongo_client, "stream_elements_commands", dict(document))

    def configured(guild_id, key, *args, **kwargs):
        if key != "stream_elements_commands":
            return {}
        return find_cog_by_guild_id(str(guild_id), key)

    asked = []

    def channel_info(name):
        asked.append(name)
        return {"_id": f"se-{name}"}

    def commands(channel_id):
        if channel_id != "se-gaules":
            return {"statusCode": 404, "error": "Not Found"}
        return [{"command": "mouse", "reply": REPLY, "enabled": True}]

    mock_cache.side_effect = configured
    monkeypatch.setattr(StreamElementsClient, "get_channel_info", staticmethod(channel_info))
    monkeypatch.setattr(StreamElementsClient, "get_chat_commands", staticmethod(commands))
    return SimpleNamespace(guild=guild, asked=asked)


async def test_an_old_document_without_its_channel_still_answers_ke_commands(
    deps, an_old_document
):
    """A document saved without its StreamElements channel finds it once and keeps it.

    Broke as: the dev bot saved StreamElements documents without `channel_id`
    (the `is_dev` skip of `_with_channel`), and the three read paths (a `ke!`
    command in chat, the panel's command list and the panel's count) read
    `channel_id` straight from the document: None, so StreamElements was asked for
    `/bot/commands/None/public`, answered 404, and nobody got an answer.

    Shared behaviour: `channel_of` (`app/services/stream_elements.py`), the one way
    the read paths learn the channel. Consumer that exposed it: stream_elements_commands
    on the dev bot.

    Guaranteed: a document without its channel still answers, the channel is looked
    up once from the streamer and written back to the document, and the next
    command reads it from there.
    """
    guild = an_old_document.guild
    member = member_without_a_picture(guild)

    first = await ask(deps.bot, guild, member, "ks!mouse")
    second = await ask(deps.bot, guild, member, "ks!mouse")

    assert reply_of(first).description == REPLY
    assert reply_of(second).description == REPLY
    stored = find_cog_by_guild_id(str(guild.id), "stream_elements_commands")
    assert stored["state"]["channel_id"] == "se-gaules", "the channel was not written back"
    assert an_old_document.asked == ["gaules"], "the channel was looked up on every message"


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(ConnectionError("StreamElements is down"), id="outage"),
        pytest.param({"statusCode": 404, "error": "Not Found"}, id="unknown-streamer"),
    ],
)
async def test_a_channel_lookup_that_failed_is_not_asked_again_on_every_message(
    deps, an_old_document, monkeypatch, answer
):
    """A failed lookup of the streamer's channel waits before StreamElements is asked again.

    Broke as: `channel_of` (`app/services/stream_elements.py`) looks the channel up
    through `find_channel` for a document saved without it, and a lookup that failed
    (StreamElements down, or a streamer it does not know) was never remembered, so
    every message with the prefix asked StreamElements again for the same channel.

    Shared behaviour: `channel_of`, read by the `ke!` listener, the panel's command
    list and the panel's count; the channel's failure mark in Redis
    (`REDIS_STREAM_ELEMENTS_CHANNEL_FAILED`, `STREAM_ELEMENTS_CHANNEL_FAILED_SECONDS`).
    Consumer that exposed it: stream_elements_commands, the architecture review of
    the beta.2 feedback.

    Guaranteed: while a lookup failed a few minutes ago, the next messages get no
    reply and StreamElements is asked for the channel once, not once per message.
    """

    def channel_info(name):
        an_old_document.asked.append(name)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(StreamElementsClient, "get_channel_info", staticmethod(channel_info))
    guild = an_old_document.guild
    member = member_without_a_picture(guild)

    messages = [await ask(deps.bot, guild, member, "ks!mouse") for _message in range(3)]

    assert all(message._reply.await_count == 0 for message in messages)
    assert an_old_document.asked == ["gaules"], (
        f"StreamElements was asked {len(an_old_document.asked)} times for one channel"
    )


def _down(channel_id):
    raise ConnectionError("StreamElements is down")


@pytest.fixture
def stream_elements_down(stream_elements_on, monkeypatch):
    """StreamElements does not answer the command list of `gaules`."""
    monkeypatch.setattr(StreamElementsClient, "get_chat_commands", staticmethod(_down))


@pytest.fixture
def no_commands(stream_elements_on, monkeypatch):
    """`gaules` has no active command on StreamElements."""
    monkeypatch.setattr(
        StreamElementsClient, "get_chat_commands", staticmethod(lambda channel_id: [])
    )


PANEL = {"streamer": "gaules", "state": {"channel_id": "se-gaules"}, "enabled": True}


async def _open_the_command_list(scenario_factory, locale="pt-br"):
    scenario = await scenario_factory(locale=locale).start_manager(
        "stream_elements_commands", PANEL
    )
    await scenario.click(ml(
        "commands.commands.commons.stream-elements-manager.commands.button.label", locale
    ))
    return [event for event in scenario.outputs if event.get("kind") == "send"][-1]


async def test_the_commands_button_sits_beside_the_loaded_commands(
    scenario_factory, no_commands
):
    """N1 (beta.2 round 2): the command list opens from a button beside the
    "Comandos carregados" block, inside the card, not from the bottom row."""
    import discord

    from tests.behavioral.harness import locators

    scenario = await scenario_factory(locale="pt-br").start_manager(
        "stream_elements_commands", PANEL
    )
    label = ml(
        "commands.commands.commons.stream-elements-manager.commands.button.label",
        "pt-br",
    )
    title = ml("commands.commands.commons.stream-elements-manager.loaded.title", "pt-br")
    card, *rows = scenario.current_message.view.children

    beside = [
        item for item in locators.walk_items(card)
        if isinstance(item, discord.ui.Section)
        and getattr(item.accessory, "label", None) == label
    ]
    assert beside, "the Commands button is not inside the card"
    assert title in beside[0].children[0].content
    bottom = [button.label for row in rows for button in row.children]
    assert label not in bottom, bottom


async def test_a_stream_elements_failure_answers_the_panel_in_the_error_format(
    scenario_factory, stream_elements_down
):
    """The panel's command list tells a StreamElements failure from a streamer without commands.

    Broke as: `_channel_commands` (`app/services/stream_elements.py`) answered `{}`
    for a payload that was not a list (a 404) and let a connection error through,
    so the panel's Commands button said "no active commands" while StreamElements
    was down, or failed outright; and "no active commands" itself was raw plain
    text, not one of Keiko's embeds.

    Shared behaviour: `StreamElementsUnavailable` and `_channel_commands`, read by
    the panel's list, the panel's count and the `ke!` listener; the error format of
    `response_error_embed`. Consumer that exposed it: stream_elements_commands
    (beta.2 feedback se-3).

    Guaranteed: a failure answers with Keiko's error embed asking to try again
    later, a streamer without commands with an embed, and neither is raw text.
    """
    sent = await _open_the_command_list(scenario_factory)

    embed = sent.get("embed") or {}
    assert sent["ephemeral"] is True
    assert not sent.get("content"), f"the failure was raw text: {sent.get('content')!r}"
    assert embed.get("color") == 0xFF0000
    assert str(embed.get("title")).startswith("🚨 ")
    assert "tente de novo mais tarde" in str(embed.get("description"))


async def test_a_streamer_without_commands_gets_an_embed_not_raw_text(
    scenario_factory, no_commands
):
    sent = await _open_the_command_list(scenario_factory)

    embed = sent.get("embed") or {}
    empty = ml("commands.commands.commons.stream-elements-manager.commands.empty", "pt-br")
    assert not sent.get("content"), f"the empty list was raw text: {sent.get('content')!r}"
    assert str(embed.get("description")).startswith(empty.replace("$streamer", "gaules"))
    assert embed.get("color") != 0xFF0000


async def test_ke_commands_tells_a_failure_from_an_empty_list(
    deps, stream_elements_down, monkeypatch
):
    guild = english_guild()
    member = member_without_a_picture(guild)

    failed = await ask(deps.bot, guild, member, "ks!commands")

    embed = reply_of(failed)
    assert embed.title.startswith("🚨 ")
    assert "try again later" in embed.description

    monkeypatch.setattr(
        StreamElementsClient, "get_chat_commands", staticmethod(lambda channel_id: [])
    )
    empty = await ask(deps.bot, guild, member, "ks!commands")

    assert empty._reply.await_count == 0, "an empty list is not a failure"


async def test_a_failure_leaves_one_warning_per_streamer(
    deps, stream_elements_down, log_channels, monkeypatch
):
    """The admin hears of a StreamElements outage once, not once per message."""
    guild = english_guild()
    member = member_without_a_picture(guild)

    for _message in range(3):
        await ask(deps.bot, guild, member, "ks!commands")

    warned = [
        embed for embed in log_channels.logs.embeds
        if "StreamElements" in str(embed.description) + str(embed.title)
    ]
    assert len(warned) == 1, f"{len(warned)} warnings for one outage"
    assert "gaules" in str(warned[0].description) + str(warned[0].title)
    assert log_channels.errors.embeds == [], "an outage is not an error of Keiko's"


async def test_an_outage_warns_again_only_once_its_mark_in_redis_is_gone(
    deps, stream_elements_down, log_channels
):
    """The warning of an outage follows a mark claimed in Redis, not a process.

    Broke as: `warn_unavailable` (`app/services/stream_elements.py`) remembered
    when it last warned in a module-level dict, so every process (and every
    restart) warned again on its own clock, and the channel lookup's failure mark
    borrowed its TTL from "no commands".

    Shared behaviour: `warn_unavailable` with `Commands.REDIS_STREAM_ELEMENTS_WARNED`
    and `Commands.STREAM_ELEMENTS_FAILURE_SECONDS`, and the channel lookup's own
    `REDIS_STREAM_ELEMENTS_CHANNEL_FAILED` with `STREAM_ELEMENTS_CHANNEL_FAILED_SECONDS`,
    met by the `ke!` listener, the panel's command list and the panel's count.
    Consumer that exposed it: stream_elements_commands (review fixes K2, item 7).

    Guaranteed: one warning while its mark lives (30 minutes), whatever process
    meets the failure, a new one once the mark expired; a failed command fetch
    never marks the channel lookup failed, whose own mark lasts 5 minutes.
    """
    from app.constants import Commands

    guild = english_guild()
    member = member_without_a_picture(guild)
    mark = Commands.REDIS_STREAM_ELEMENTS_WARNED.format(streamer="gaules")
    channel = Commands.REDIS_STREAM_ELEMENTS_CHANNEL_FAILED.format(streamer="gaules")

    def warnings():
        return [
            embed for embed in log_channels.logs.embeds
            if "StreamElements" in str(embed.description) + str(embed.title)
        ]

    await ask(deps.bot, guild, member, "ks!commands")
    assert deps.redis_client.get(mark), "the warning left no mark in Redis"
    assert deps.redis_client.ttl(mark) == 60 * 30
    assert not deps.redis_client.get(channel), "a failed fetch blocked channel lookups"
    await ask(deps.bot, guild, member, "ks!commands")
    assert len(warnings()) == 1

    deps.redis_client.delete(mark)
    await ask(deps.bot, guild, member, "ks!commands")

    assert len(warnings()) == 2, "the outage was not told again once its mark expired"


async def test_a_failed_channel_lookup_is_marked_for_five_minutes(
    deps, an_old_document, monkeypatch
):
    from app.constants import Commands

    def channel_info(name):
        raise ConnectionError("StreamElements is down")

    monkeypatch.setattr(StreamElementsClient, "get_channel_info", staticmethod(channel_info))
    guild = an_old_document.guild

    await ask(deps.bot, guild, member_without_a_picture(guild), "ks!mouse")

    channel = Commands.REDIS_STREAM_ELEMENTS_CHANNEL_FAILED.format(streamer="gaules")
    warned = Commands.REDIS_STREAM_ELEMENTS_WARNED.format(streamer="gaules")
    assert deps.redis_client.ttl(channel) == 60 * 5
    assert deps.redis_client.ttl(warned) == 60 * 30

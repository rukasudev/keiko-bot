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

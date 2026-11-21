"""Keiko's answer to a chat message that mentions him: a 🐶 reaction and, once in ten
mentions, a bark in reply that pings nobody."""

import logging
import random
from types import SimpleNamespace

import discord
import pytest

from app.constants import Commands
from app.core.events import Events
from app.core.utils import ml
from tests.mocks.discord import MockRole, create_guild, create_member, create_message

pytestmark = pytest.mark.unit

KEIKO = 1047382926158254191
KEIKO_ROLE = 1047382926158254999
SOMEONE = 1047382926158254555
BARKS = ["once", "twice"]


def in_a_server(deps, content, channel_denies=()):
    """A member's message in a server where Keiko holds its own role."""
    deps.bot.user = SimpleNamespace(id=KEIKO)
    guild = create_guild()
    guild.self_role = MockRole(id=KEIKO_ROLE, name="Keiko", position=5)
    channel = guild.text_channels[0]
    channel.denied = set(channel_denies)
    return create_message(channel, create_member(guild, id=111), content)


class TestTheMentionReaction:
    """A message that mentions Keiko gets Keiko's reaction."""

    @pytest.mark.parametrize(
        "content",
        [f"<@{KEIKO}> bom dia!", f"bom dia <@!{KEIKO}>", f"<@&{KEIKO_ROLE}> oi"],
        ids=["mention", "nickname-mention", "keiko-role"],
    )
    async def test_a_message_that_mentions_keiko_gets_a_reaction(
        self, deps, nothing_on, content
    ):
        message = in_a_server(deps, content)

        await Events(deps.bot).on_message(message)

        assert message.reactions == [Commands.MENTION_REACTION]
        message.channel.assert_no_message_sent()

    @pytest.mark.parametrize(
        "content",
        ["bom dia!", f"<@{SOMEONE}> bom dia!", f"<@&{KEIKO_ROLE + 1}> oi"],
        ids=["no-mention", "someone-else", "another-role"],
    )
    async def test_a_message_that_does_not_mention_keiko_gets_nothing(
        self, deps, nothing_on, content
    ):
        message = in_a_server(deps, content)

        await Events(deps.bot).on_message(message)

        assert message.reactions == []

    async def test_a_reply_that_pings_keiko_only_through_the_reply_gets_nothing(
        self, deps, nothing_on
    ):
        message = in_a_server(deps, "valeu!")
        message.reference = SimpleNamespace(message_id=999)
        message.mentions = [deps.bot.user]

        await Events(deps.bot).on_message(message)

        assert message.reactions == []

    async def test_a_direct_message_gets_nothing(self, deps, nothing_on):
        message = in_a_server(deps, f"<@{KEIKO}> oi")
        message.guild = None
        message.channel.guild = None

        await Events(deps.bot).on_message(message)

        assert message.reactions == []

    async def test_a_channel_without_add_reactions_gets_nothing(
        self, deps, nothing_on, rolled
    ):
        rolled(0.0)
        message = in_a_server(
            deps, f"{deps.bot.config.PREFIX}oi <@{KEIKO}>", ("add_reactions",)
        )

        await Events(deps.bot).on_message(message)

        assert message.reactions == []
        message.channel.assert_no_message_sent()
        assert message._reply.await_count == 0


class TestTheWoof:
    """Once in `Commands.MENTION_WOOF_CHANCE` mentions, the reaction comes with a bark."""

    @pytest.mark.parametrize("bark", BARKS)
    async def test_a_lucky_mention_gets_a_bark_that_pings_nobody_and_stays(
        self, deps, nothing_on, rolled, bark
    ):
        rolled(0.0, BARKS.index(bark))
        message = in_a_server(deps, f"<@{KEIKO}> bom dia!")

        await Events(deps.bot).on_message(message)

        assert message.reactions == [Commands.MENTION_REACTION]
        assert message._reply.await_args.kwargs == {
            "content": ml(f"help.ui.texts.woof.{bark}", "pt-br"),
            "mention_author": False,
        }

    @pytest.mark.parametrize(
        "roll, barks",
        [(0.0, 1), (0.0999, 1), (0.1, 0), (0.99, 0)],
        ids=["lowest", "just-under-a-tenth", "a-tenth", "highest"],
    )
    async def test_one_mention_in_ten_gets_a_bark(
        self, deps, nothing_on, rolled, roll, barks
    ):
        rolled(roll)
        message = in_a_server(deps, f"<@{KEIKO}> oi")

        await Events(deps.bot).on_message(message)

        assert message.reactions == [Commands.MENTION_REACTION]
        assert message._reply.await_count == barks

    async def test_a_channel_where_keiko_cannot_write_keeps_the_reaction_alone(
        self, deps, nothing_on, rolled, caplog
    ):
        rolled(0.0)
        message = in_a_server(deps, f"<@{KEIKO}> oi", ("send_messages",))
        message._reply.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            {"code": 50013, "message": "Missing Permissions"},
        )

        with caplog.at_level(logging.WARNING):
            await Events(deps.bot).on_message(message)

        assert message.reactions == [Commands.MENTION_REACTION]
        assert message._reply.await_count == 1
        assert [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ] == []


@pytest.fixture
def nothing_on(mock_cache):
    """A server with no feature on."""
    mock_cache.return_value = {}
    return mock_cache


@pytest.fixture
def rolled(monkeypatch):
    """Pin the mention's roll to `value` and the bark to the one at `index`."""

    def pin(value, index=0):
        monkeypatch.setattr(random, "random", lambda: value)
        monkeypatch.setattr(random, "choice", lambda options: options[index])

    return pin

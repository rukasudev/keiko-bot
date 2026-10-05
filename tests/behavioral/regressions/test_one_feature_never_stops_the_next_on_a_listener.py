"""One part of a listener failing never stops the next part of that listener.

Reported in the v1 review: `on_member_join` ran default roles and then the welcome
message under one `with_error_context`, and `on_message` the StreamElements reply and
then the block links check. When the first one raised (Keiko lost the permission to give
roles, StreamElements did not answer), the second never ran: the new member got no
welcome, and a blocked link stayed up. The next review found the same in the other
listeners: a server leaving whose snapshot or flush failed was never paused, a server
joining whose record failed was never greeted, and `with_error_context` was a second
listener context that raised where `work.listener` logs.

Shared behaviour: the unit of work a listener runs for one server (`work.listener`,
`app/services/work.py`), behind all six listeners of `app/cogs/events.py`. Exposed by:
the reviews of the listeners.

Guaranteed: each part of a listener runs on its own; a failure is an error with the
listener's context (its guild, member and channel, never the message's text), nothing
is raised to discord.py, and the next part still runs: the welcome reaches its channel,
the blocked link is deleted, the server Keiko left is paused, the new server is greeted.
"""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.mocks.discord import create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

BLOCK_ALL = {
    "mode": "block_all",
    "allowed_roles": {"values": []},
    "allowed_chats": {"values": []},
    "allowed_links": {"values": []},
    "answer": "No links here, {user}!",
}


@pytest.fixture
def events(deps, guild):
    from app.cogs.events import Events

    guild.owner = create_member(guild, id=313, name="Owner")
    guild.preferred_locale = discord.Locale.american_english
    deps.bot.config.PREFIX = "ke!"
    deps.bot.user = SimpleNamespace(id=99)
    deps.bot.owner_id = 1
    deps.bot.guilds = []
    return Events(deps.bot)


def failures(caplog):
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def broken(error):
    def fail(*args, **kwargs):
        raise error

    return fail


async def test_a_new_member_is_welcomed_when_giving_roles_fails(
    events, guild, channel, mock_cache, mock_banner, monkeypatch, caplog
):
    member = create_member(guild, id=999, name="Newcomer")
    member._user = MagicMock(id=999)
    mock_cache.return_value = {
        "welcome_messages_channel": {"values": str(channel.id)},
        "welcome_messages": {"values": "Welcome {user}!"},
        "welcome_messages_title": "Welcome!",
        "welcome_messages_footer": "Enjoy your stay!",
    }
    monkeypatch.setattr("app.cogs.events.get_available_roles_by_guild", lambda guild: ["Member"])
    monkeypatch.setattr(
        "app.services.default_roles.set_on_member_join",
        AsyncMock(side_effect=RuntimeError("Missing Permissions")),
    )

    with caplog.at_level("INFO"):
        await events.on_member_join(member)

    channel.assert_message_sent()
    assert channel.get_last_embed().title == "Welcome!"
    failed = failures(caplog)
    assert failed and failed[0].context.guild_id == str(guild.id)
    assert failed[0].context.user_id == str(member.id)


async def test_a_message_is_checked_for_blocked_links_when_the_reply_fails(
    events, guild, channel, mock_cache, monkeypatch, caplog
):
    author = create_member(guild, id=111, name="Member")
    message = create_message(channel, author, "ke!mouse https://blocked.example")
    mock_cache.return_value = dict(BLOCK_ALL)
    monkeypatch.setattr(
        "app.services.stream_elements.check_message",
        AsyncMock(side_effect=RuntimeError("StreamElements did not answer")),
    )

    with caplog.at_level("INFO"):
        await events.on_message(message)

    message.assert_deleted()
    assert author.mention in str(channel._sent_messages[-1].content)
    failed = failures(caplog)
    assert failed and failed[0].context.channel_id == str(message.channel.id)
    assert "blocked.example" not in caplog.text


@pytest.mark.parametrize("failing", ["report", "snapshot"])
async def test_a_server_keiko_left_is_paused_whatever_failed_before(
    events, deps, guild, monkeypatch, caplog, failing
):
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": str(guild.id), "is_bot_online": True,
    })
    if failing == "report":
        monkeypatch.setattr("app.cogs.events.features_on", broken(RuntimeError("Mongo hung up")))
    else:
        monkeypatch.setattr(
            "app.services.analytics_reports.guild_snapshot", broken(RuntimeError("Mongo hung up"))
        )

    with caplog.at_level("INFO"):
        await events.on_guild_remove(guild)

    record = deps.mongo_client.guild.moderations.find_one({"guild_id": str(guild.id)})
    assert record["is_bot_online"] is False, "the pause ran after the failure"
    failed = failures(caplog)
    assert failed and failed[0].context.guild_id == str(guild.id)
    assert failed[0].context.extra["part"] == failing


async def test_a_new_server_is_greeted_when_recording_it_fails(
    events, guild, channel, monkeypatch, caplog
):
    monkeypatch.setattr("app.cogs.events.join_guild", broken(RuntimeError("Mongo hung up")))

    with caplog.at_level("INFO"):
        await events.on_guild_join(guild)

    channel.assert_message_sent()
    failed = failures(caplog)
    assert failed and failed[0].context.extra["part"] == "record"


async def test_an_edited_message_check_that_fails_is_an_error_with_its_context(
    events, guild, channel, monkeypatch, caplog
):
    monkeypatch.setattr(
        "app.services.block_links.check_edited_message",
        AsyncMock(side_effect=RuntimeError("Discord is down")),
    )
    payload = SimpleNamespace(guild_id=guild.id, channel_id=channel.id, message_id=7)

    with caplog.at_level("INFO"):
        await events.on_raw_message_edit(payload)

    [failed] = failures(caplog)
    assert failed.context.guild_id == str(guild.id)
    assert failed.context.channel_id == str(channel.id)


async def test_a_command_count_that_fails_is_an_error_with_its_context(
    events, guild, channel, monkeypatch, caplog
):
    monkeypatch.setattr("app.cogs.events.increment_redis_key", broken(RuntimeError("Redis is down")))
    interaction = SimpleNamespace(
        type=discord.InteractionType.application_command,
        command=SimpleNamespace(_attr="block_links", qualified_name="moderations block links"),
        guild_id=guild.id,
        channel_id=channel.id,
        user=create_member(guild, id=222, name="Admin"),
        extras={},
    )

    with caplog.at_level("INFO"):
        await events.on_interaction(interaction)

    [failed] = failures(caplog)
    assert failed.context.guild_id == str(guild.id)
    assert failed.context.user_id == "222"
    assert failed.context.channel_id == str(channel.id)

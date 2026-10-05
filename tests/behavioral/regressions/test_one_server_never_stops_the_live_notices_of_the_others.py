"""One server Keiko cannot reach never stops a Twitch notice for the others.

Reported in the v1 review: `process_notifications` posted a live to every following
server in one loop with nothing between them, so a server Keiko had left
(`bot.get_guild` returning None), a deleted channel or a 403 stopped the notice for
every server after it; production holds a Twitch document still on in a guild Keiko
left. The offline edit walked the same loop and reassigned `status` inside it, so a
streamer followed in two channels got "🔴 🔴 offline | ⌛️ duration: 2h | ⌛️ Duration:
2h" on the second message; that footer was English, written in Python.

Shared behaviour: the per-server fan-out every notice now runs through
(`app/services/work.py`, the generic part of the YouTube announcement), and the
Twitch notifier on top of it. Exposed by: the production guild Keiko left.

Guaranteed: each server gets its notice and its status on its own; a server Keiko
cannot reach is a warning, one that refuses is counted as a permission failure, and
anything else is an error with its context, and the next server still hears. The
status is written once per message, in the server's language, from the language files.
"""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from app.services.notifications_twitch import (
    handle_send_streamer_notification,
    handle_send_streamer_offline_notification,
)
from tests.mocks.discord import create_guild

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("fan_out"),
]

GONE = 4004


@pytest.fixture
def servers(deps):
    """Two servers following `gaules`, and the streamer live for two hours."""
    first = create_guild(id=5001, name="First", channels=["lives"])
    second = create_guild(id=5002, name="Second", channels=["streams", "more-streams"])
    known = {first.id: first, second.id: second}
    deps.bot.get_guild = lambda guild_id: known.get(int(guild_id))
    deps.twitch.add_user("gaules", user_id="123")
    deps.twitch.set_stream_online(
        "gaules", game="CS2", started_at=datetime.now(timezone.utc) - timedelta(hours=2)
    )
    for guild in known.values():
        guild.preferred_locale = discord.Locale.american_english
        for channel in guild.text_channels:
            remember_sent_messages(channel)
    return SimpleNamespace(first=first, second=second)


def remember_sent_messages(channel):
    """Discord finds a message by its id whether it is given as text or as a number."""
    channel._fetch_message.side_effect = lambda message_id: next(
        (sent for sent in channel._sent_messages if str(sent.id) == str(message_id)), None
    )


def follow(deps, guild_id, *channels):
    deps.mongo_client.guild.notifications_twitch.insert_one({
        "guild_id": str(guild_id),
        "enabled": True,
        "notifications": {"values": [
            {
                "streamer": {"value": "gaules"},
                "channel": {"value": str(channel)},
                "notification_messages": {"value": "{streamer} is live! {stream_link}"},
            }
            for channel in channels
        ]},
    })


def notices(guild):
    return [message for channel in guild.text_channels for message in channel._sent_messages]


def footers(guild):
    return [message.embeds[0].footer.text for message in notices(guild)]


def errors(caplog, flow="twitch_notification"):
    return [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == flow
    ]


def refuse(channel):
    channel._send.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
    )


async def test_a_server_keiko_left_does_not_stop_the_live_notice_of_the_next(
    deps, servers, caplog
):
    follow(deps, GONE, 100)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.second)) == 1, "the server after the missing one still hears"
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == [], (
        "a server Keiko left is a warning, not an error"
    )


async def test_a_server_that_refuses_the_notice_does_not_stop_the_next(
    deps, servers, analytics_events, caplog
):
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)
    refuse(servers.first.text_channels[0])

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.second)) == 1
    refused = [event["guild_id"] for event in analytics_events
               if event["event"] == "value.blocked_by_permission"]
    assert refused == [str(servers.first.id)]
    assert errors(caplog) == [], "a missing permission is a warning, not an error"


async def test_an_unexpected_failure_is_an_error_with_its_context_and_the_next_still_hears(
    deps, servers, caplog
):
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)
    servers.first.text_channels[0]._send.side_effect = RuntimeError("the embed is too long")

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.second)) == 1
    failed = errors(caplog)
    assert [record.context.guild_id for record in failed] == [str(servers.first.id)]
    assert failed[0].exc_info is not None, "the traceback travels with the error"


async def test_a_streamer_followed_in_several_channels_gets_its_status_once_per_message(
    deps, servers
):
    follow(deps, servers.second.id, *(channel.id for channel in servers.second.text_channels))
    await handle_send_streamer_notification("gaules")

    await handle_send_streamer_offline_notification("gaules")

    written = footers(servers.second)
    assert [footer.count("🔴") for footer in written] == [1, 1]
    assert [footer.count("Duration") for footer in written] == [1, 1]
    assert all(footer.startswith("• 🔴 Offline | ⌛️ Duration: 2h") for footer in written)


async def test_the_status_speaks_the_servers_language(deps, servers):
    servers.second.preferred_locale = discord.Locale.brazil_portuguese
    follow(deps, servers.second.id, servers.second.text_channels[0].id)

    await handle_send_streamer_notification("gaules")
    assert footers(servers.second) == ["• 🟢 Ao vivo"]

    await handle_send_streamer_offline_notification("gaules")
    assert footers(servers.second)[0].startswith("• 🔴 Live encerrada | ⌛️ Duração: 2h")


async def test_the_offline_edit_skips_a_server_keiko_left_and_reaches_the_next(
    deps, servers, caplog
):
    follow(deps, servers.second.id, servers.second.text_channels[0].id)
    await handle_send_streamer_notification("gaules")
    follow(deps, GONE, 100)
    deps.mongo_client.notifications.notifications_twitch.insert_one({
        "guild_id": str(GONE), "channel_id": "100", "streamer": "gaules", "message_id": "1",
    })
    reordered = sorted(
        deps.mongo_client.guild.notifications_twitch._data,
        key=lambda document: document["guild_id"] != str(GONE),
    )
    deps.mongo_client.guild.notifications_twitch._data[:] = reordered

    with caplog.at_level("INFO"):
        await handle_send_streamer_offline_notification("gaules")

    assert footers(servers.second)[0].startswith("• 🔴 Offline")
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []

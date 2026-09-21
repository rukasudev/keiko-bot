"""A synchronous call inside a coroutine stops the whole bot, not one command.

Everything runs on one event loop: the gateway heartbeat, every interaction,
every listener. `requests`, `pymongo` and `time.sleep` are all synchronous, so a
coroutine that reaches them directly freezes the bot for as long as they take.

Production showed both halves of the damage:

- 52 `heartbeat blocked for more than 20 seconds` warnings in one day, each with
  `on_message -> check_message -> get_cog_data_or_populate -> find_one` in the
  traceback, ending in two restarts;
- a `10062 Unknown interaction` on a guild that had just subscribed a youtuber.
  Discord gives an interaction three seconds to be acknowledged, and two
  `requests.post` calls had already spent them. The youtuber was saved; the
  person saw "This interaction failed" and started over.

These tests do not assert that a helper was called. They run the real entry
point against a call that blocks, and count how many times the event loop came
back to life while it ran. A blocked loop cannot count.
"""
import asyncio
import socket
import threading
import time
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import redis
import requests
from PIL import Image

from app.constants import Commands as commands_constants
from app.services import (
    block_links,
    default_roles,
    notifications_twitch,
    stream_elements,
    welcome_messages,
)
from app.constants import DBConfigs
from tests.mocks import create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("event_loop")]

REAL_CREATE_BANNER = welcome_messages.create_banner

BLOCKING_SECONDS = 0.25
TICK_SECONDS = 0.005
# A loop that never yields ticks zero or one time; a healthy one ticks ~50.
MIN_TICKS = 5


async def ticks_while(awaitable):
    """How many times the loop got control back while `awaitable` ran."""
    ticks = 0
    running = True

    async def heartbeat():
        nonlocal ticks
        while running:
            await asyncio.sleep(TICK_SECONDS)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    try:
        await awaitable
    finally:
        running = False
        beat.cancel()

    return ticks


def blocking(return_value=None):
    """A stand-in for `requests`, `find_one` or `time.sleep`."""
    def call(*args, **kwargs):
        time.sleep(BLOCKING_SECONDS)
        return return_value
    return call


async def test_checking_a_message_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, monkeypatch
):
    """`on_message` runs for every message in every guild, and it reads Mongo."""
    mock_cache.side_effect = blocking({})
    message = create_message(content="hello", author=member, channel=channel)

    ticks = await ticks_while(block_links.check_message(str(guild.id), message))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: the gateway heartbeat was "
        f"blocked for the whole read, which is what restarted the bot on 31/08"
    )


async def test_a_stream_elements_command_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, monkeypatch
):
    mock_cache.side_effect = blocking({})
    message = create_message(content="ks!mouse", author=member, channel=channel)

    ticks = await ticks_while(
        stream_elements.check_message(str(guild.id), message, "ks!")
    )

    assert ticks >= MIN_TICKS


async def test_answering_a_stream_elements_command_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, monkeypatch
):
    """The cache read is not the only blocking call on this path.

    A `ks!` command that misses the cache goes to the StreamElements API over
    `requests`, from the same coroutine, right after the read that was fixed.
    """
    mock_cache.return_value = {"streamer": "gaules", "channel_id": "c1", "enabled": True}
    monkeypatch.setattr(
        "app.services.stream_elements.get_reply_in_cache_or_populate", blocking(None)
    )
    message = create_message(content="ks!mouse", author=member, channel=channel)

    ticks = await ticks_while(
        stream_elements.check_message(str(guild.id), message, "ks!")
    )

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: the API call beside the cache "
        f"read blocks the gateway just the same"
    )


async def test_listing_stream_elements_commands_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, monkeypatch
):
    mock_cache.return_value = {"streamer": "gaules", "channel_id": "c1", "enabled": True}
    monkeypatch.setattr(
        "app.services.stream_elements.get_commands_in_cache_or_populate", blocking([])
    )
    message = create_message(content="ks!commands", author=member, channel=channel)

    ticks = await ticks_while(
        stream_elements.check_message(str(guild.id), message, "ks!")
    )

    assert ticks >= MIN_TICKS


async def test_a_birthday_reminder_never_freezes_the_bot(deps, monkeypatch):
    """The webhook job runs on the loop, and reads Mongo once per guild."""
    from app.webhooks import birthday_handler

    monkeypatch.setattr(
        "app.data.birthdays.find_birthday_items_by_date", blocking([])
    )

    ticks = await ticks_while(
        birthday_handler.process_birthday_webhook("reminder-1", "03-15")
    )

    assert ticks >= MIN_TICKS


async def test_waiting_for_a_stream_never_freezes_the_bot(deps, monkeypatch):
    """The retry loop sleeps 15 seconds at a time, twice, between Twitch calls."""
    monkeypatch.setattr(
        "app.services.notifications_twitch.wait_for_stream_info", blocking(None)
    )

    ticks = await ticks_while(
        notifications_twitch.handle_send_streamer_notification("gaules")
    )

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: a `time.sleep` in a coroutine "
        f"stops the gateway, not just this notification"
    )


async def test_subscribing_a_youtuber_never_freezes_the_bot(deps, monkeypatch):
    """The reported bug: two blocking POSTs between the click and the ack.

    The platform commits an added item through its feature module, which
    runs the subscription in a worker thread; a blocking subscribe must
    still leave the loop free to acknowledge the interaction.
    """
    from datetime import datetime, timezone

    from app.settings.form.form_state import Answer
    from app.settings.features import feature_for
    from app.settings.features.feature import CommitContext

    deps.bot.config.is_dev = lambda: False
    feature = feature_for(commands_constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY)
    monkeypatch.setattr(feature, "subscribe", blocking())
    context = CommitContext(
        "123456789", "555", "pt-br", {}, datetime.now(timezone.utc), "slash", "s1"
    )
    item = {"channel": Answer("100"), "youtuber": Answer("sondureacts"),
            "notification_messages": Answer("oi")}

    ticks = await ticks_while(feature.commit("add_item", {"answers": item}, context))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: Discord's three second budget "
        f"is spent inside the subscribe, so the acknowledgement arrives too late"
    )


async def test_a_member_joining_a_welcome_server_never_freezes_the_bot(
    deps, mock_cache, guild, bot, member
):
    """`on_member_join` reads the welcome configuration for every new member."""
    mock_cache.side_effect = blocking({})

    ticks = await ticks_while(welcome_messages.send_welcome_message(member))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: every member join froze the "
        f"gateway for the whole configuration read"
    )


async def test_a_member_joining_a_default_roles_server_never_freezes_the_bot(
    deps, mock_cache, guild, bot, member
):
    mock_cache.side_effect = blocking({})

    ticks = await ticks_while(default_roles.set_on_member_join(member))

    assert ticks >= MIN_TICKS


async def test_syncing_default_roles_never_freezes_the_bot(
    deps, mock_cache, guild, bot, interaction
):
    mock_cache.side_effect = blocking({})

    ticks = await ticks_while(default_roles.set_on_default_roles_sync(interaction))

    assert ticks >= MIN_TICKS


async def test_previewing_a_welcome_message_never_freezes_the_bot(
    deps, mock_cache, guild, bot, interaction
):
    mock_cache.side_effect = blocking({})

    ticks = await ticks_while(
        welcome_messages.send_welcome_message_preview(interaction, [])
    )

    assert ticks >= MIN_TICKS


async def test_checking_why_a_link_passed_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, interaction
):
    mock_cache.side_effect = blocking({})
    message = create_message(content="https://example.com", author=member, channel=channel)

    ticks = await ticks_while(block_links.send_link_check_message(interaction, message))

    assert ticks >= MIN_TICKS


async def test_drawing_a_welcome_banner_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, monkeypatch
):
    """A member join draws a banner: two image downloads and an upload."""
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "orange").save(buffer, format="PNG")

    def slow_download(url, *args, **kwargs):
        if "/avatars/" in url:
            time.sleep(BLOCKING_SECONDS)
        response = requests.Response()
        response.status_code = 200
        response._content = buffer.getvalue()
        return response

    monkeypatch.setattr(welcome_messages, "create_banner", REAL_CREATE_BANNER)
    monkeypatch.setattr(requests, "get", slow_download)
    bot.get_channel.return_value.send = AsyncMock(
        return_value=SimpleNamespace(
            attachments=[SimpleNamespace(url="https://cdn.discordapp.com/attachments/9/9/b.png")]
        )
    )
    mock_cache.return_value = {
        "welcome_messages_channel": {"values": str(channel.id)},
        "welcome_messages": {"values": "Welcome {user}!"},
        "welcome_messages_title": "Welcome!",
        "welcome_design": "server_blur",
    }
    member = create_member(guild, id=880011, name="Slowpoke")
    member._user = SimpleNamespace(id=880011)

    ticks = await ticks_while(welcome_messages.send_welcome_message(member))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: the avatar download ran on the "
        f"loop, freezing the bot on every member join"
    )


def test_a_stalled_redis_gives_up_instead_of_holding_a_thread(monkeypatch):
    """Cache reads run in threads; a Redis that never answers must not keep them."""
    from app import connect_redis

    monkeypatch.setattr(DBConfigs, "REDIS_SOCKET_TIMEOUT_SECONDS", 0.2)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    accepted = []

    def serve():
        while True:
            try:
                accepted.append(listener.accept()[0])
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    try:
        client = connect_redis(f"redis://127.0.0.1:{listener.getsockname()[1]}/0")
        started = time.monotonic()
        with pytest.raises(redis.exceptions.TimeoutError):
            client.get("guild:1:cog.block_links")
        assert time.monotonic() - started < 3
    finally:
        listener.close()
        for connection in accepted:
            connection.close()

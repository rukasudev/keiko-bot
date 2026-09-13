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
import time

import pytest

from app.constants import Commands as commands_constants
from app.services import block_links, notifications_twitch, stream_elements
from tests.mocks import create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("event_loop")]

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

    from app.forms.engine.session import Answer
    from app.forms.features import feature_for
    from app.forms.features.protocol import CommitContext

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

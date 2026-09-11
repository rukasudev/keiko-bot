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
from app.views.form import Form
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
    """The reported bug: two blocking POSTs between the click and the ack."""
    deps.bot.config.is_dev.return_value = False
    monkeypatch.setattr(
        "app.services.notifications_youtube_video.handle_subscribe_youtubers_new_video",
        blocking(),
    )

    form = Form.__new__(Form)
    form.command_key = commands_constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY
    form.responses = [{"value": [{"youtuber": {"value": "sondureacts"}}]}]
    form.view = type("V", (), {"form_view": type("F", (), {})()})()

    ticks = await ticks_while(form.pre_finish_step(interaction=None))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: Discord's three second budget "
        f"is spent inside the subscribe, so the acknowledgement arrives too late"
    )

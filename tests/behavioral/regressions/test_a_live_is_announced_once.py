"""A Twitch live is announced once, however many deliveries of it overlap.

Reported in the v1 review of the loop and runtime PR: the live notice decides it is a new
stream by the date of the last one, and writes that date only after the fan-out. Twitch
delivers an event again when it is not sure the first delivery arrived, so two
deliveries of one `stream.online` that overlapped both read the old date and both posted
the notice in every following server.

Shared behaviour: `handle_send_streamer_notification`
(`app/services/notifications_twitch.py`), behind the Twitch webhook. Exposed by: the
review, next to the claim the YouTube announcement already takes on its video.

Guaranteed: before the fan-out, the notice claims the stream (its streamer and when it
started) in Redis, as the YouTube announcement claims its video; a delivery that finds
the claim taken posts nothing. The claim is taken only once the followers are read, so a
delivery that could not read them (found in the next review: its claim held the live for
a day) leaves the live to the next delivery. When Redis cannot answer, the live is still
announced, with a warning, rather than missed.
"""
import asyncio
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.notifications_twitch import handle_send_streamer_notification
from tests.behavioral.regressions.test_one_server_never_stops_the_live_notices_of_the_others import (  # noqa: F401
    follow,
    notices,
    servers,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("fan_out"),
]


async def test_two_deliveries_of_one_live_at_once_post_it_once(deps, servers):
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)

    await asyncio.gather(
        handle_send_streamer_notification("gaules"),
        handle_send_streamer_notification("gaules"),
    )

    assert len(notices(servers.first)) == 1
    assert len(notices(servers.second)) == 1


async def test_a_delivery_that_could_not_read_its_followers_leaves_the_live_to_the_next(
    deps, servers, monkeypatch
):
    from app.services import notifications_twitch

    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    read = notifications_twitch.following_guilds
    failures = iter([RuntimeError("Mongo hung up")])

    def following_guilds(streamer_name):
        for failure in failures:
            raise failure
        return read(streamer_name)

    monkeypatch.setattr(notifications_twitch, "following_guilds", following_guilds)

    with pytest.raises(RuntimeError):
        await handle_send_streamer_notification("gaules")
    await handle_send_streamer_notification("gaules")

    assert len(notices(servers.first)) == 1, "the second delivery posts the live"


async def test_a_live_is_announced_when_redis_cannot_say_whether_it_was(
    deps, servers, monkeypatch, caplog
):
    follow(deps, servers.first.id, servers.first.text_channels[0].id)

    def down(*args, **kwargs):
        raise RedisConnectionError("Redis is down")

    monkeypatch.setattr(deps.redis_client, "set", down)

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.first)) == 1, "better twice than never"
    assert any(
        record.levelno == logging.WARNING and "**gaules**" in record.getMessage()
        for record in caplog.records
    )

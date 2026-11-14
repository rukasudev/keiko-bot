"""A server is counted as greeted only once its greeting is on a channel.

Broke as: `GreetingsView.send` emitted `guild.greeting_sent` with
`channel_found=True` before `channel.send`, so a greeting Discord refused (a
channel override the permission check does not see, an outage) was still counted
as delivered, and the guild profile stamped `greeting_sent_at` for a server that
never saw it.

Shared behaviour: the onboarding greeting (app/views/greetings.py), sent by
`Events.on_guild_join`, and the guild profile and admin digest that read its
event (app/core/analytics_sink.py, app/services/admin_analytics.py).

Guaranteed: a greeting that reached a channel is counted once with
`channel_found=True`; a greeting whose send failed is never counted as sent; a
server with no writable channel is still counted with `channel_found=False`.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from tests.generators.moderations import recorded_server

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = 987


def _guild(db, *channels):
    guild = SimpleNamespace(id=GUILD_ID, text_channels=list(channels), me=SimpleNamespace())
    recorded_server(db, guild.id)
    return guild


def _channel(writable=True, send=None):
    return SimpleNamespace(
        permissions_for=lambda _member: SimpleNamespace(send_messages=writable),
        send=send or AsyncMock(),
    )


def _greetings(recorded):
    return [
        event["props"]["channel_found"]
        for event in recorded
        if event["type"] == "guild.greeting_sent"
    ]


async def test_a_greeting_discord_refused_is_never_counted_as_sent(deps, analytics_events):
    from app.views.greetings import GreetingsView

    refused = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")
    guild = _guild(deps.mongo_client, _channel(send=AsyncMock(side_effect=refused)))

    with pytest.raises(discord.Forbidden):
        await GreetingsView().send(guild)

    assert _greetings(analytics_events) == [], (
        "a greeting Discord refused was counted as posted on a channel"
    )


async def test_a_greeting_on_a_channel_is_counted_once(deps, analytics_events):
    from app.views.greetings import GreetingsView

    channel = _channel()
    guild = _guild(deps.mongo_client, _channel(writable=False), channel)

    await GreetingsView().send(guild)

    channel.send.assert_awaited_once()
    assert _greetings(analytics_events) == [True]


async def test_a_server_without_a_writable_channel_is_counted_as_not_found(
    deps, analytics_events
):
    from app.views.greetings import GreetingsView

    guild = _guild(deps.mongo_client, _channel(writable=False))

    await GreetingsView().send(guild)

    assert _greetings(analytics_events) == [False]

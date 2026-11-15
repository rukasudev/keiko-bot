"""`/config update` changes one part of Keiko's presence and keeps the other two.

Broke as: `/config update status` sent the status alone, so Discord dropped the
activity line until a restart; `/config update activity` and `description` rebuilt the
activity from the one Keiko logged in with, so each undid the other, and a gateway
reconnect brought back the boot presence, since nothing told the client what runs now.

Shared behaviour: `update_status`, `update_activity` and `update_description`
(`app/commands/config/service.py`) over `discord.Client.change_presence`.
Exposed by: typing the config service under mypy strict, where the status and the
activity turned out to be the command's choices, not discord's types.

Guaranteed: each command sends the whole presence, the part it changes next to what
the last command left, and a reconnect identifies with that same presence.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.app_commands import Choice

from app.commands.config.service import update_activity, update_description, update_status

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


@pytest.fixture
def keiko():
    client = discord.Client(
        intents=discord.Intents.none(),
        status=discord.Status.dnd,
        activity=discord.Activity(type=discord.ActivityType.watching, name="/help"),
    )
    client.ws = SimpleNamespace(change_presence=AsyncMock())
    return client


def _sent(keiko):
    presence = keiko.ws.change_presence.await_args.kwargs
    activity = presence["activity"]
    return presence["status"], activity.type, activity.name


async def test_a_status_change_keeps_the_activity(keiko):
    await update_status(keiko, Choice(name="idle", value="idle"))

    assert _sent(keiko) == ("idle", discord.ActivityType.watching, "/help")


async def test_an_activity_and_a_description_keep_each_other(keiko):
    await update_activity(keiko, Choice(name="listening", value=2))
    await update_description(keiko, "lo-fi")

    assert _sent(keiko) == ("dnd", discord.ActivityType.listening, "lo-fi")


async def test_a_reconnect_identifies_with_the_presence_set_last(keiko):
    await update_status(keiko, Choice(name="idle", value="idle"))
    await update_activity(keiko, Choice(name="playing", value=0))

    assert keiko.status is discord.Status.idle
    assert (keiko.activity.type, keiko.activity.name) == (
        discord.ActivityType.playing, "/help"
    )

"""The log inspection dates a feature that is on from the last time it came on.

Broke as: `_find_last_enabled_event` walked the events newest first, the order
`find_cog_events_by_guild` reads them in, and kept the last match, so a feature
paused and unpaused yesterday read as on "since" its first setup months ago.

Shared behaviour: the commands status of `LogInspectionView`
(app/views/log_inspection.py), read by the /admin inspect button of every log
message, over the per-feature event history every lifecycle commit writes.

Guaranteed: the status line of a feature that is on names its newest enable or
unpause, whatever older events the history holds.
"""
from datetime import datetime, timedelta, timezone

import discord
import pytest

from app.constants import Commands
from app.core.utils import format_relative_time

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "777"


async def test_a_feature_that_is_on_is_dated_from_its_latest_enable(deps):
    from app.views.log_inspection import LogInspectionView

    now = datetime.now(timezone.utc)
    first_setup = now - timedelta(days=90)
    unpaused = now - timedelta(days=1)
    history = [
        {"guild_id": GUILD_ID, "event": "enabled", "datetime": first_setup},
        {"guild_id": GUILD_ID, "event": "paused", "datetime": now - timedelta(days=2)},
        {"guild_id": GUILD_ID, "event": "unpaused", "datetime": unpaused},
        {"guild_id": GUILD_ID, "event": "edited", "datetime": now - timedelta(hours=1)},
    ]
    deps.mongo_client.events[Commands.BLOCK_LINKS_KEY].insert_many(history)
    embed = discord.Embed()

    LogInspectionView(None, None, GUILD_ID)._add_commands_status(
        embed, {Commands.BLOCK_LINKS_KEY: True}
    )

    [status] = embed.fields
    [line] = [
        line for line in status.value.split("\n") if Commands.BLOCK_LINKS_KEY in line
    ]
    assert line == f"✅ {Commands.BLOCK_LINKS_KEY} ({format_relative_time(unpaused)})", (
        "the inspection dated block links from its first setup, not its latest unpause"
    )

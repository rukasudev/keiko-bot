"""`/admin overview` reads Discord's cache only on the event loop that owns it.

Reported in the v1 review of the loop and runtime PR: once the overview moved off the
loop, the whole of it ran on a `keiko-io` thread, its walk over `bot.guilds` and each
guild's channels included. discord.py changes that cache from the loop, so a server
joining or a channel created during the walk could stop it with "dictionary changed size
during iteration".

Shared behaviour: `get_overview_data` (`app/services/admin.py`), behind `/admin overview`.
Exposed by: the review of what moved to a thread.

Guaranteed: the overview counts servers, members and channels on the loop, and hands only
the Mongo, Redis and Twitch reads to a thread.
"""
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from app.services import admin
from tests.mocks.discord import MockInteraction, create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


class Cache(list):
    """Discord's cache of servers, noting the thread of everyone who walks it."""

    def __init__(self, guilds, walkers):
        super().__init__(guilds)
        self.walkers = walkers

    def __iter__(self):
        self.walkers.append(threading.current_thread())
        return super().__iter__()


@pytest.fixture
def stores(monkeypatch):
    monkeypatch.setattr(admin, "mongo_client", SimpleNamespace(guild=SimpleNamespace(
        moderations=SimpleNamespace(aggregate=lambda pipeline: [])
    )))
    monkeypatch.setattr(admin, "redis_client", SimpleNamespace(
        scan_iter=lambda pattern: [], get=lambda key: None, dbsize=lambda: 0
    ))
    monkeypatch.setattr(admin, "build_product_summary", lambda: "")


async def test_the_overview_walks_discords_cache_only_on_the_loop(deps, stores):
    from app.cogs.admin import Admin

    walkers = []
    guild = create_guild(id=4242, name="Busy server")
    server = SimpleNamespace(
        id=4242, name="Busy server", member_count=10, text_channels=[], voice_channels=[]
    )
    bot = SimpleNamespace(
        ready_time=datetime.now() - timedelta(hours=1),
        latency=0.05,
        status=discord.Status.online,
        activity=SimpleNamespace(name="Keiko"),
        guilds=Cache([server], walkers),
        extensions={},
        get_guild=lambda guild_id: None,
        twitch=SimpleNamespace(get_subscriptions=lambda: {"data": []}),
        config=SimpleNamespace(ENVIRONMENT="test"),
    )
    cog = Admin.__new__(Admin)
    cog.bot = bot
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.command = SimpleNamespace(qualified_name="admin overview")

    await Admin.show_overview.callback(cog, interaction)

    loop = threading.current_thread()
    assert walkers and all(walker is loop for walker in walkers), [w.name for w in walkers]
    assert interaction.get_last_response()["type"] == "followup_send"

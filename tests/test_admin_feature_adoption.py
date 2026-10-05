"""The overview's feature adoption counts a feature as on by the record the bot runs on.

Reported in the v1 review: `/admin overview` summed the old moderations flags of the
servers marked online, which wave 1 stopped trusting (`is_feature_on` is the one source
of "on"). A feature disabled while its flag stayed on was counted, one saved without the
flag (StreamElements, welcome after the v1 migration) was not, and a server Keiko had
left but whose record still said online counted too.

Shared behaviour: `get_overview_data` (`app/services/admin.py`). Exposed by: the wave 1
review of every reader of the old flags.

Guaranteed: each feature counts the servers Keiko is in whose saved settings
`is_feature_on` calls on, a document saved before `enabled` following its old flag.
The Mongo aggregation the overview runs cannot run offline, so the test answers it the
way Mongo does for the flags below and checks the adoption does not come from it.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from app.constants import Commands
from app.services import admin

pytestmark = pytest.mark.unit

STAYED = "6001"
LEFT = "6002"
FLAGS_AS_MONGO_SUMS_THEM = {
    "guild_status": [{"_id": True, "count": 1}],
    "top_owner": [],
    "feature_adoption": [{
        "_id": None,
        Commands.BLOCK_LINKS_KEY: 1,
        Commands.DEFAULT_ROLES_KEY: 1,
        Commands.WELCOME_MESSAGES_KEY: 0,
    }],
    "newest_guild": [],
    "monthly_growth": [],
}


def server(guild_id):
    return SimpleNamespace(
        id=int(guild_id), name=f"Server {guild_id}", member_count=10,
        text_channels=[], voice_channels=[],
    )


@pytest.fixture
def bot():
    def no_twitch():
        raise RuntimeError("Twitch is not reached offline")

    return SimpleNamespace(
        ready_time=datetime.now() - timedelta(hours=1),
        latency=0.05,
        status=discord.Status.online,
        activity=SimpleNamespace(name="Keiko"),
        guilds=[server(STAYED)],
        extensions={},
        get_guild=lambda guild_id: None,
        twitch=SimpleNamespace(get_subscriptions=no_twitch),
    )


@pytest.fixture
def stores(deps, monkeypatch):
    monkeypatch.setattr(admin, "mongo_client", SimpleNamespace(guild=SimpleNamespace(
        moderations=SimpleNamespace(aggregate=lambda pipeline: [FLAGS_AS_MONGO_SUMS_THEM])
    )))
    monkeypatch.setattr(admin, "redis_client", SimpleNamespace(
        scan_iter=lambda pattern: [], get=lambda key: None, dbsize=lambda: 0
    ))
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": STAYED,
        "is_bot_online": True,
        Commands.BLOCK_LINKS_KEY: True,
        Commands.DEFAULT_ROLES_KEY: True,
        Commands.WELCOME_MESSAGES_KEY: False,
    })
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": LEFT, "is_bot_online": True, Commands.WELCOME_MESSAGES_KEY: True}
    )
    feature = deps.mongo_client.guild
    feature[Commands.BLOCK_LINKS_KEY].insert_one({"guild_id": STAYED, "enabled": False})
    feature[Commands.DEFAULT_ROLES_KEY].insert_one({"guild_id": STAYED})
    feature[Commands.WELCOME_MESSAGES_KEY].insert_one({"guild_id": STAYED, "enabled": True})
    feature[Commands.WELCOME_MESSAGES_KEY].insert_one({"guild_id": LEFT, "enabled": True})


async def test_a_feature_counts_where_its_saved_settings_say_on_in_a_server_keiko_is_in(
    bot, stores
):
    adoption = (await admin.get_overview_data(bot))["feature_adoption"]

    assert adoption[Commands.WELCOME_MESSAGES_KEY] == 1, "on without the old flag, once"
    assert adoption[Commands.BLOCK_LINKS_KEY] == 0, "disabled while its flag stayed on"
    assert adoption[Commands.DEFAULT_ROLES_KEY] == 1, "saved before `enabled`: its flag"


async def test_the_overview_reads_two_fields_of_each_feature_document(
    bot, stores, deps, monkeypatch
):
    """A feature document holds whole lists (links, birthdays, messages); the adoption
    needs to know only whose it is and whether it is on."""
    asked = []
    for key in Commands.COMMANDS_LIST:
        collection = deps.mongo_client.guild[key]
        monkeypatch.setattr(
            collection, "find",
            lambda query, projection, read=collection.find: (asked.append(projection), read(query))[1],
        )

    await admin.get_overview_data(bot)

    assert asked and all(
        projection == {"_id": False, "guild_id": True, "enabled": True} for projection in asked
    )

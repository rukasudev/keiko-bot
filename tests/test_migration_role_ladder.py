"""data/0009 gives every auto roles document its roles by time.

Guaranteed: an auto roles document saved before the roles by time holds the empty
ladder the setup now saves, in its stored shape; a document with a ladder keeps its
rungs; no other feature's document changes; a second run writes nothing. The offline
run goes through `up` over the project's Mongo fake; the mongo half runs against a
throwaway replica set (MONGO_TEST_URL), like the runner's.
"""

import copy
import importlib

import pytest

from tests.generators.moderations import ladder_rung
from tests.helpers.migrations import migrate as migrated
from tests.helpers.migrations import run_offline, throwaway_mongo
from tests.mocks.database import MockMongoClient

MIGRATION = importlib.import_module("app.migrations.data.0009_role_ladder")

BEFORE_THE_LADDER = {
    "_id": "1001:default_roles",
    "guild_id": "1001",
    "cog": "default_roles",
    "enabled": True,
    "options": {"default_roles": {"style": "role", "values": ["202"]}},
    "state": {},
}
WITH_A_RUNG = {
    "_id": "1002:default_roles",
    "guild_id": "1002",
    "cog": "default_roles",
    "enabled": True,
    "options": {
        "default_roles": {"style": "role", "values": []},
        "role_ladder": {"style": "composition", "values": [ladder_rung(7, "202")]},
    },
    "state": {},
}
ANOTHER_FEATURE = {
    "_id": "1001:block_links",
    "guild_id": "1001",
    "cog": "block_links",
    "enabled": True,
    "options": {"mode": "block_all"},
    "state": {},
}
SAVED = (BEFORE_THE_LADDER, WITH_A_RUNG, ANOTHER_FEATURE)


@pytest.fixture
def offline(monkeypatch):
    return run_offline(monkeypatch, MIGRATION)


def saved_before():
    client = MockMongoClient()
    client.guild.cogs.insert_many(copy.deepcopy(list(SAVED)))
    return client


def options(client, cog_id):
    return client.guild.cogs.find_one({"_id": cog_id})["options"]


def test_a_document_from_before_the_ladder_gets_the_empty_one(offline):
    client = saved_before()

    MIGRATION.up(client)

    assert options(client, BEFORE_THE_LADDER["_id"])["role_ladder"] == {
        "style": "composition", "values": [],
    }
    assert options(client, WITH_A_RUNG["_id"]) == WITH_A_RUNG["options"]
    assert options(client, ANOTHER_FEATURE["_id"]) == ANOTHER_FEATURE["options"]
    assert offline[0].counts == {"ladders_added": 1}


def test_a_second_run_changes_nothing(offline):
    client = saved_before()
    MIGRATION.up(client)
    migrated = copy.deepcopy(client.guild.cogs.find({})._data)

    MIGRATION.up(client)

    assert client.guild.cogs.find({})._data == migrated
    assert offline[1].counts == {"ladders_added": 0}


@pytest.fixture
def server():
    client = throwaway_mongo(__name__)
    yield client
    client.close()


@pytest.mark.mongo
def test_the_ladder_is_written_on_a_real_replica_set(server):
    migrated(server, "data/0009")
    server.migrations.changelog.delete_one({"_id": "data/0009"})
    server.guild.cogs.insert_many(copy.deepcopy(list(SAVED)))

    counts = migrated(server, "data/0009")

    assert counts == {"ladders_added": 1}
    stored = server.guild.cogs.find_one({"_id": BEFORE_THE_LADDER["_id"]})
    assert stored["options"]["role_ladder"] == {"style": "composition", "values": []}

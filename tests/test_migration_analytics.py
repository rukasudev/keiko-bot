"""data/0007 copies the numbers of before into the analytics database.

Guaranteed: each month bucket becomes the server's counts for that month as it is; each
profile becomes the server's lifetime with `setup_opened_at` read as `opened_at`, its
admins kept once each up to the cap `Commands` names and counted, and a feature the
server has on again no longer stamped as disabled or paused; Keiko's block links totals
become `blocked:total` and `blocked:site:{host}`; `check` refuses what it cannot copy;
a run from empty writes nothing, a second run writes nothing new, and the old
collections stay a release.

The mongo half runs against a throwaway replica set (MONGO_TEST_URL), like the runner's.
"""

import importlib
import io
import os
from datetime import datetime

import pytest
from pymongo import MongoClient

from app.constants import Commands, Migrations
from app.migrations import runner

MIGRATION = importlib.import_module("app.migrations.data.0007_analytics")
MARKER = "keiko_migrations_test"
JOINED = datetime(2026, 3, 1, 12, 0)
OPENED = datetime(2026, 3, 2, 12, 0)
DISABLED = datetime(2026, 4, 1, 12, 0)


def profile(guild_id, **fields):
    return {
        "_id": guild_id,
        "joined_at": JOINED,
        "removed_at": None,
        "admins": ["9", "8", "9"],
        "totals": {"setups_completed": 1, "value_events": 4},
        "features": {
            "welcome_messages": {
                "setup_opened_at": OPENED,
                "completed_at": OPENED,
                "disabled_at": DISABLED,
                "value_count": 4,
            },
            "block_links": {"setup_opened_at": OPENED, "disabled_at": DISABLED},
        },
        "updated_at": DISABLED,
        **fields,
    }


def test_the_admins_cap_is_the_one_the_sink_keeps():
    assert MIGRATION.ADMINS_MAX == Commands.ANALYTICS_LIFETIME_ADMINS_MAX


def test_a_profile_becomes_a_lifetime_with_the_new_names():
    lifetime, cleared = MIGRATION.lifetime_document(profile("1001"), {"welcome_messages"})

    assert cleared == 1
    assert lifetime == {
        "_id": "1001",
        "joined_at": JOINED,
        "removed_at": None,
        "admins": ["9", "8"],
        "admins_count": 2,
        "totals": {"setups_completed": 1, "value_events": 4},
        "features": {
            "welcome_messages": {
                "opened_at": OPENED,
                "completed_at": OPENED,
                "disabled_at": None,
                "value_count": 4,
            },
            "block_links": {"opened_at": OPENED, "disabled_at": DISABLED},
        },
        "updated_at": DISABLED,
    }


def test_the_admins_are_capped_and_counted_up_to_the_cap():
    many = [str(index) for index in range(MIGRATION.ADMINS_MAX + 5)]

    lifetime, _ = MIGRATION.lifetime_document(profile("1001", admins=many), set())

    assert lifetime["admins"] == many[: MIGRATION.ADMINS_MAX]
    assert lifetime["admins_count"] == MIGRATION.ADMINS_MAX


def test_keikos_totals_take_the_ids_the_sink_counts_by():
    assert MIGRATION.keiko_total({"_id": "total", "blocked": 9}) == {
        "_id": "blocked:total", "blocked": 9,
    }
    assert MIGRATION.keiko_total(
        {"_id": "site:spam-site.com", "host": "spam-site.com", "blocked": 4}
    ) == {"_id": "blocked:site:spam-site.com", "host": "spam-site.com", "blocked": 4}


def test_a_blocked_link_counted_as_an_action_is_counted_as_link_blocked():
    """Broke as: block links counted every blocked link as `feature.action_performed`,
    and the sink now counts `link.blocked`, so a copy as it was would split one
    server's blocks over two names. Guaranteed: the old name lands under the new one,
    added to it when a day holds both, and every other metric is copied as it is."""
    bucket = {
        "_id": "1001|2026-10", "guild_id": "1001", "month": "2026-10",
        "days": {
            "03": {"feature_action_performed|block_links": 4, "guild_joined": 1},
            "04": {
                "feature_action_performed|block_links": 2,
                "link_blocked|block_links": 1,
            },
        },
    }

    document, renamed = MIGRATION.counts_document(bucket)

    assert document["days"] == {
        "03": {"link_blocked|block_links": 4, "guild_joined": 1},
        "04": {"link_blocked|block_links": 3},
    }
    assert renamed == 2
    assert document["_id"] == bucket["_id"] and document["month"] == "2026-10"


def test_the_check_refuses_what_it_cannot_copy():
    problems = MIGRATION.problems_in(
        [
            {"_id": "1001|2026-10", "guild_id": "1001", "month": "2026-10", "days": {}},
            {"_id": "1001|2026-11", "guild_id": "1002", "month": "2026-11", "days": {}},
        ],
        [profile("1001"), profile(1002), profile("1003", features=["block_links"])],
        [
            {"_id": "total", "blocked": 3},
            {"_id": "site:x.io", "blocked": 1},
            {"_id": "total", "blocked": "3"},
        ],
    )

    assert problems == [
        "guild.analytics_guild_month '1001|2026-11': not a {guild}|{month} bucket "
        "with its days",
        "guild.analytics_guild_profile 1002: not a guild id",
        "guild.analytics_guild_profile 1003: no map of features",
        "guild.block_links_totals 'site:x.io': no total and no site",
        "guild.block_links_totals 'total': no total and no site",
    ]


@pytest.fixture
def server():
    if not os.getenv("MONGO_TEST_URL"):
        pytest.skip("MONGO_TEST_URL names no throwaway Mongo")
    client = MongoClient(os.environ["MONGO_TEST_URL"], serverSelectionTimeoutMS=5000)
    names = set(client.list_database_names()) - Migrations.SYSTEM_DATABASES
    if names and MARKER not in names:
        pytest.exit(
            f"MONGO_TEST_URL holds {sorted(names)} and no {MARKER}: not a throwaway server",
            returncode=3,
        )
    client[MARKER].owner.replace_one(
        {"_id": "owner"}, {"_id": "owner", "suite": __name__}, upsert=True
    )
    for name in set(client.list_database_names()) - Migrations.SYSTEM_DATABASES - {MARKER}:
        client.drop_database(name)
    yield client
    client.close()


def migrate(client):
    versions, problems = runner.read_versions()
    out = io.StringIO()
    assert not problems and runner.migrate(client, versions, runner.Report(out)), (
        out.getvalue()
    )
    return client.migrations.changelog.find_one({"_id": "data/0007"})["counts"]


def production_shaped(client):
    client.guild.analytics_guild_month.insert_many([
        {
            "_id": "1001|2026-10",
            "guild_id": "1001",
            "month": "2026-10",
            "days": {"03": {"value_delivered|welcome_messages": 5, "guild_joined": 1}},
            "updated_at": DISABLED,
        },
        {
            "_id": "1002|2026-11",
            "guild_id": "1002",
            "month": "2026-11",
            "days": {"12": {
                "command_invoked|block_links": 2,
                "feature_action_performed|block_links": 6,
            }},
            "updated_at": DISABLED,
        },
    ])
    client.guild.analytics_guild_profile.insert_many([
        profile("1001"),
        profile("1002", removed_at=DISABLED, admins=[]),
    ])
    client.guild.cogs.insert_one({
        "_id": "1001:welcome_messages",
        "guild_id": "1001",
        "cog": "welcome_messages",
        "enabled": True,
        "options": {},
        "state": {},
    })
    client.guild.block_links_totals.insert_many([
        {"_id": "total", "blocked": 12, "updated_at": DISABLED},
        {"_id": "site:spam-site.com", "host": "spam-site.com", "blocked": 9},
        {"_id": "site:x.io", "host": "x.io", "blocked": 3},
    ])


@pytest.mark.mongo
def test_from_empty_the_file_writes_nothing(server):
    counts = migrate(server)

    assert counts == {
        "counts": 0,
        "metrics_renamed": 0,
        "lifetimes": 0,
        "features_on_again": 0,
        "blocked_totals": 0,
    }
    for name in ("counts", "lifetime", "keiko"):
        assert server.analytics[name].count_documents({}) == 0


@pytest.mark.mongo
def test_production_numbers_land_in_the_analytics_database(server):
    production_shaped(server)

    counts = migrate(server)

    assert counts == {
        "counts": 2,
        "metrics_renamed": 1,
        "lifetimes": 2,
        "features_on_again": 1,
        "blocked_totals": 3,
    }
    assert server.analytics.counts.find_one({"_id": "1001|2026-10"}) == (
        server.guild.analytics_guild_month.find_one({"_id": "1001|2026-10"})
    )
    assert server.analytics.counts.find_one({"_id": "1002|2026-11"})["days"]["12"] == {
        "command_invoked|block_links": 2, "link_blocked|block_links": 6,
    }
    first = server.analytics.lifetime.find_one({"_id": "1001"})
    assert first["features"]["welcome_messages"]["opened_at"] == OPENED
    assert first["features"]["welcome_messages"]["disabled_at"] is None
    assert first["features"]["block_links"]["disabled_at"] == DISABLED
    assert (first["admins"], first["admins_count"]) == (["9", "8"], 2)
    assert server.analytics.lifetime.find_one({"_id": "1002"})["admins_count"] == 0
    assert {
        document["_id"]: document["blocked"] for document in server.analytics.keiko.find({})
    } == {"blocked:total": 12, "blocked:site:spam-site.com": 9, "blocked:site:x.io": 3}
    assert server.guild.analytics_guild_profile.count_documents({}) == 2, (
        "the old documents stay a release"
    )


@pytest.mark.mongo
def test_a_second_run_over_the_same_documents_writes_nothing_new(server):
    production_shaped(server)
    migrate(server)
    server.migrations.changelog.delete_one({"_id": "data/0007"})
    server.analytics.lifetime.update_one({"_id": "1001"}, {"$set": {"admins_count": 7}})

    counts = migrate(server)

    assert counts["counts"] == 0 and counts["lifetimes"] == 0
    assert counts["blocked_totals"] == 0
    assert server.analytics.lifetime.find_one({"_id": "1001"})["admins_count"] == 7


@pytest.mark.mongo
def test_a_document_it_cannot_copy_stops_the_run_before_any_write(server):
    server.guild.analytics_guild_profile.insert_one(profile(1002))
    versions, _ = runner.read_versions()
    out = io.StringIO()

    assert not runner.migrate(server, versions, runner.Report(out))
    assert "guild.analytics_guild_profile 1002: not a guild id" in out.getvalue()
    assert "analytics" not in server.list_database_names() or (
        server.analytics.lifetime.count_documents({}) == 0
    )

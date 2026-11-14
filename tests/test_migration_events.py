"""data/0006 copies every event Keiko kept into events.changes, events.activity and
events.interactions, in the envelope `analytics.record` writes.

Guaranteed: each events.<cog> and events.settings row becomes one change with its id, its
date (its id's time when the date was never written), its server, its cog and its member;
each guild.analytics_events row goes to the collection the catalog names today, with
`source` and `result` in its props and `event_id`, `env` and `app_version` left behind,
except the lifecycle events, which the audit rows already copy; each guild.blocked_links
row becomes a `link.blocked` activity carrying its host, link, rule, mode, reason, match,
deleted and channel; each audit.errors row inside the 90 days becomes a `command.failed`
with its error type and no traceback, unless analytics already holds that failure; the
file's mappings agree with the catalog and the constants; `check` refuses a row it cannot
map; a run from empty writes nothing, and a second run writes nothing new.

The mongo half runs against a throwaway replica set (MONGO_TEST_URL), like the runner's.
"""

import importlib
import io
import os
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.constants import Commands, Migrations
from app.core import analytics
from app.core.cogs import LIFECYCLE_ANALYTICS_EVENTS
from app.migrations import runner

MIGRATION = importlib.import_module("app.migrations.data.0006_events")
MARKER = "keiko_migrations_test"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
TRACEBACK = (
    "The following command raised an exception: **moderations block links**"
    "```KeyError: 'allowed_links'```\n**Traceback**```File \"app/x.py\", line 3```"
)


def audit(event, guild_id="1", user_id="555", moment=NOW, cog="block_links"):
    return {
        "_id": ObjectId(),
        "guild_id": guild_id,
        "cog_key": cog,
        "user_id": user_id,
        "datetime": moment,
        "event": event,
    }


def analytics_event(event, moment=NOW, **fields):
    return {
        "_id": ObjectId(),
        "event": event,
        "event_id": "ab" * 16,
        "v": 1,
        "ts": moment,
        "guild_id": "1",
        "user_id": "9",
        "actor": "user",
        "feature": "block_links",
        "source": "slash",
        "session_id": "s1",
        "result": None,
        "props": {},
        "env": "prod",
        "app_version": "v1.0.0",
        **fields,
    }


def blocked_row(moment=NOW, **fields):
    return {
        "_id": ObjectId(),
        "guild_id": "1",
        "user_id": "777",
        "channel_id": "100",
        "message_id": "500",
        "link": "https://spam.com/promo",
        "host": "spam.com",
        "mode": "block_all",
        "reason": "blocked-no-rule",
        "rule": None,
        "match": None,
        "deleted": False,
        "created_at": moment,
        "updated_at": moment,
        **fields,
    }


def error_row(moment=NOW, command="block_links", message=TRACEBACK):
    return {
        "_id": ObjectId(),
        "created_at": moment,
        "updated_at": moment,
        "error_message": message,
        "command_key": command,
    }


def test_an_audit_row_becomes_the_change_it_recorded():
    row = audit("paused", moment=NOW)

    assert MIGRATION.change_from_audit("block_links", row) == {
        "_id": row["_id"],
        "ts": NOW,
        "type": "feature.paused",
        "guild_id": "1",
        "cog": "block_links",
        "actor": {"kind": "user", "id": "555"},
        "trace_id": None,
        "session_id": None,
        "props": {},
        "v": 1,
    }


def test_an_audit_row_without_a_date_takes_the_time_of_its_id():
    row = audit("edited", moment=None)

    change = MIGRATION.change_from_audit("block_links", row)

    assert change["ts"] == row["_id"].generation_time


def test_an_analytics_event_moves_its_envelope_into_the_new_shape():
    row = analytics_event(
        "setup.completed", result="success", props={"steps_viewed": 4}, guild_id=10
    )

    document = MIGRATION.from_analytics_event(row)

    assert document == {
        "_id": row["_id"],
        "ts": NOW,
        "type": "setup.completed",
        "guild_id": "10",
        "cog": "block_links",
        "actor": {"kind": "user", "id": "9"},
        "trace_id": None,
        "session_id": "s1",
        "props": {"steps_viewed": 4, "source": "slash", "result": "success"},
        "v": 1,
    }


def test_an_event_keiko_caused_names_the_bot_and_no_member():
    row = analytics_event("value.blocked_by_permission", actor="system", source=None)

    document = MIGRATION.from_analytics_event(row)

    assert document["actor"] == {"kind": "bot", "id": None}
    assert "source" not in document["props"]


def test_a_blocked_link_row_becomes_a_link_blocked_activity():
    row = blocked_row()

    document = MIGRATION.link_blocked(row)

    assert document["type"] == "link.blocked"
    assert document["cog"] == "block_links"
    assert document["actor"] == {"kind": "user", "id": "777"}
    assert document["ts"] == NOW
    assert document["props"] == {
        "channel_id": "100",
        "link": "https://spam.com/promo",
        "host": "spam.com",
        "mode": "block_all",
        "reason": "blocked-no-rule",
        "deleted": False,
    }


def test_an_error_row_becomes_a_failure_with_its_type_and_no_traceback():
    document = MIGRATION.failure_from_error(error_row())

    assert document["type"] == "command.failed"
    assert document["props"] == {"command": "block_links", "error_type": "KeyError"}
    assert document["guild_id"] is None
    assert "Traceback" not in str(document) and "allowed_links" not in str(document)


def test_a_failure_analytics_already_holds_is_recognised_by_command_and_time():
    failure = MIGRATION.failure_from_error(error_row())
    same = analytics_event(
        "command.failed", moment=NOW + timedelta(seconds=2), feature="block_links"
    )
    other = analytics_event(
        "command.failed", moment=NOW + timedelta(seconds=2), feature="welcome_messages"
    )
    later = analytics_event(
        "command.failed", moment=NOW + timedelta(minutes=1), feature="block_links"
    )

    assert MIGRATION.is_recorded(failure, [same])
    assert not MIGRATION.is_recorded(failure, [other, later])


def test_the_files_mappings_agree_with_the_catalog_and_the_constants():
    """A migration file holds literals only, so this is what keeps them true."""
    catalog = analytics.events_catalog()
    lifecycle = set(LIFECYCLE_ANALYTICS_EVENTS.values())

    assert MIGRATION.LIFECYCLE == LIFECYCLE_ANALYTICS_EVENTS
    for name, collection in MIGRATION.COLLECTIONS.items():
        expected = "audit" if name in lifecycle else catalog[name]["collection"]
        assert collection == expected, name
    documented = {name for name, declaration in catalog.items()
                  if declaration["collection"] != "none" and name != "link.blocked"}
    assert documented <= set(MIGRATION.COLLECTIONS), "an event the file cannot map"
    assert MIGRATION.RETENTION_DAYS * 86400 == Commands.EVENTS_INTERACTIONS_TTL_SECONDS
    assert MIGRATION.AUDIT_COGS == (
        *sorted(spec["command_key"] for spec in Commands.SETUP_FEATURES),
        Commands.SERVER_SETTINGS_KEY,
    )


def test_the_check_refuses_what_it_cannot_map():
    bad_audit = audit("renamed")
    undated_audit = audit("enabled") | {"datetime": "2026-01-01", "_id": "plain"}
    serverless = audit("enabled", guild_id=None)
    unknown = analytics_event("command.renamed")
    strange_actor = analytics_event("command.invoked", actor="cron")
    undated = analytics_event("command.invoked", moment="yesterday")
    link = blocked_row(guild_id=None)
    error = error_row(moment="today") | {"_id": "e1"}

    problems = MIGRATION.problems_in(
        [("block_links", bad_audit), ("settings", undated_audit), ("block_links", serverless)],
        [unknown, strange_actor, undated, analytics_event("command.invoked")],
        [link, blocked_row()],
        [error, error_row()],
    )

    assert problems == [
        f"events.block_links {bad_audit['_id']}: unknown event 'renamed'",
        "events.settings plain: datetime is not a date",
        f"events.block_links {serverless['_id']}: no guild_id",
        f"guild.analytics_events {unknown['_id']}: unknown event 'command.renamed'",
        f"guild.analytics_events {strange_actor['_id']}: unknown actor 'cron'",
        f"guild.analytics_events {undated['_id']}: ts is not a date",
        f"guild.blocked_links {link['_id']}: no created_at or guild_id",
        "audit.errors e1: created_at is not a date",
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
    assert not problems and runner.migrate(client, versions, runner.Report(out)), out.getvalue()
    return client.migrations.changelog.find_one({"_id": "data/0006"})["counts"]


def production_shaped(client):
    """Rows in the shapes production holds them, one of each case the file maps."""
    client.events.block_links.insert_many([
        audit("enabled", moment=NOW - timedelta(days=200)),
        audit("paused", user_id=999, moment=NOW - timedelta(days=3)),
        audit("edited", moment=None),
    ])
    client.events.welcome_messages.insert_one(audit("enabled", cog="welcome_messages"))
    client.events.settings.insert_one(audit("edited", cog="settings"))
    client.guild.analytics_events.insert_many([
        analytics_event("feature.enabled"),
        analytics_event("setup.completed", props={"steps_viewed": 4}),
        analytics_event("command.failed", moment=NOW - timedelta(days=1),
                        props={"command": "moderations block links",
                               "error_type": "KeyError"}),
        analytics_event("guild.joined", actor="system", user_id=None, feature=None,
                        source=None, props={"returning": False}),
        analytics_event("value.blocked_by_permission", actor="system",
                        props={"error_type": "Forbidden"}),
    ])
    client.guild.blocked_links.insert_many([blocked_row(), blocked_row(deleted=True)])
    client.audit.errors.insert_many([
        error_row(moment=NOW - timedelta(days=1)),
        error_row(moment=NOW - timedelta(days=2), command="welcome_messages"),
        error_row(moment=NOW - timedelta(days=200)),
    ])


EMPTY_COUNTS = {
    "changes_from_audit": 0,
    "changes_from_analytics": 0,
    "lifecycle_events_left_to_audit": 0,
    "activity_from_analytics": 0,
    "links_blocked": 0,
    "interactions_from_analytics": 0,
    "failures_from_errors": 0,
    "errors_past_retention": 0,
    "errors_already_recorded": 0,
}


@pytest.mark.mongo
def test_from_empty_the_file_writes_nothing(server):
    counts = migrate(server)

    assert counts == EMPTY_COUNTS
    for name in ("changes", "activity", "interactions"):
        assert server.events[name].count_documents({}) == 0


@pytest.mark.mongo
def test_production_rows_land_in_their_new_collections(server):
    production_shaped(server)

    counts = migrate(server)

    assert counts == {
        "changes_from_audit": 5,
        "changes_from_analytics": 1,
        "lifecycle_events_left_to_audit": 1,
        "activity_from_analytics": 1,
        "links_blocked": 2,
        "interactions_from_analytics": 2,
        "failures_from_errors": 1,
        "errors_past_retention": 1,
        "errors_already_recorded": 1,
    }
    changes = list(server.events.changes.find({"guild_id": "1"}).sort("ts", -1))
    assert {change["type"] for change in changes} == {
        "feature.enabled", "feature.paused", "config.changed", "guild.joined",
    }
    paused = server.events.changes.find_one({"type": "feature.paused"})
    assert paused["actor"] == {"kind": "user", "id": "999"}
    assert server.events.changes.count_documents({"cog": "settings"}) == 1
    joined = server.events.changes.find_one({"type": "guild.joined"})
    assert joined["actor"] == {"kind": "bot", "id": None}
    assert joined["props"] == {"returning": False}
    assert server.events.activity.count_documents({"type": "link.blocked"}) == 2
    failure = server.events.interactions.find_one(
        {"type": "command.failed", "cog": "welcome_messages"}
    )
    assert failure["props"] == {"command": "welcome_messages", "error_type": "KeyError"}
    for name in ("changes", "activity", "interactions"):
        for document in server.events[name].find({}):
            assert not {"event", "event_id", "env", "app_version"} & set(document), name
    assert server.guild.analytics_events.count_documents({}) == 5, (
        "the old rows stay a release"
    )


@pytest.mark.mongo
def test_the_copied_events_are_what_the_readers_find(server, monkeypatch):
    from app.core import cogs as cogs_service
    from app.data.events import activity, changes

    production_shaped(server)
    migrate(server)
    monkeypatch.setattr(changes, "mongo_client", server)
    monkeypatch.setattr(activity, "mongo_client", server)

    history = cogs_service.find_changes("1", "block_links")
    blocks = activity.find_by_guild("1", "block_links", ["link.blocked"])

    assert [change["type"] for change in history] == [
        "config.changed", "feature.paused", "feature.enabled"
    ]
    assert len(blocks) == 2


@pytest.mark.mongo
def test_a_second_run_over_the_same_rows_writes_nothing_new(server):
    production_shaped(server)
    migrate(server)
    server.migrations.changelog.delete_many({"_id": {"$gte": "data/0006", "$lt": "data/~"}})

    counts = migrate(server)

    assert counts["changes_from_audit"] == 0 and counts["links_blocked"] == 0
    assert counts["interactions_from_analytics"] == 0
    assert counts["failures_from_errors"] == 0


@pytest.mark.mongo
def test_a_row_it_cannot_map_stops_the_run_before_any_write(server):
    server.events.block_links.insert_one(audit("renamed"))
    versions, _ = runner.read_versions()
    out = io.StringIO()

    assert not runner.migrate(server, versions, runner.Report(out))
    assert "unknown event 'renamed'" in out.getvalue()
    assert server.events.changes.count_documents({}) == 0

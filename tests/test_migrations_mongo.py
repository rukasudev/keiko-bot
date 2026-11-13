"""The migrations runner against a real single-node replica set, skipped without one.

Set MONGO_TEST_URL to a throwaway server (CI starts one). The suite drops every
database it finds there, so it refuses a server holding data it did not write.

Guaranteed: from empty to the latest files and back to "nothing to apply" with no
count changed; every pending schema file runs before the first pending data file; the
ledger names each file by its folder; a failing data file leaves no ledger entry and
none of its writes; a second runner stops on the lock; a dry run writes nothing yet
reports real counts; verify passes after a run and fails once an index is dropped; the
live snapshot prints exactly the committed one; a live index with the same name and
another spec, duplicates a unique index would refuse, or a second bot document stop
the run before anything is written.
"""

import io
import json
import os
import threading
from datetime import datetime, timezone

import pytest
from pymongo import MongoClient

from app.constants import Migrations
from app.migrations import runner
from app.migrations.__main__ import main
from app.migrations.helpers import begin, index

MARKER = "keiko_migrations_test"

pytestmark = [
    pytest.mark.mongo,
    pytest.mark.skipif(
        not os.getenv("MONGO_TEST_URL"),
        reason="MONGO_TEST_URL names no throwaway Mongo",
    ),
]


@pytest.fixture(scope="module")
def server():
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
    yield client
    client.close()


@pytest.fixture
def client(server):
    for name in (
        set(server.list_database_names()) - Migrations.SYSTEM_DATABASES - {MARKER}
    ):
        server.drop_database(name)
    return server


@pytest.fixture
def versions():
    found, problems = runner.read_versions()
    assert problems == []
    return found


def run(client, versions, **options):
    out = io.StringIO()
    passed = runner.migrate(client, versions, runner.Report(out), **options)
    return passed, out.getvalue()


def live(client):
    structure = runner.live_structure(client)
    structure.pop(MARKER, None)
    return structure


def counts(client):
    return {
        f"{database}.{collection}": client[database][collection].count_documents({})
        for database, collections in live(client).items()
        for collection in collections
    }


def ledger(client):
    return runner.read_ledger(client)


def ledger_ids(client):
    return [entry["_id"] for entry in ledger(client)]


def databases(client):
    return set(client.list_database_names()) - Migrations.SYSTEM_DATABASES - {MARKER}


def schema_file(number, name, structure):
    return runner.Version("schema", f"{number:04d}", name, "c" * 64, structure)


def data_file(number, name, up):
    return runner.Version("data", f"{number:04d}", name, "d" * 64, up=up)


def shipped_ids(versions, kind=None):
    return sorted(found.id for found in versions if kind in (None, found.kind))


def next_number(versions, kind):
    return 1 + max(int(found.number) for found in versions if found.kind == kind)


def test_from_empty_to_the_latest_files_the_database_is_the_snapshot(
    client, versions
):
    passed, out = run(client, versions)

    assert passed, out
    assert ledger_ids(client) == shipped_ids(versions)
    assert live(client) == runner.expected_structure(versions)
    assert runner.render(live(client)) == runner.SNAPSHOT.read_text()
    bot = client.configs.data.find_one({}, {"_id": False})
    assert bot == {
        "prefix": "ke!",
        "status": "online",
        "activity": 0,
        "description": "/help",
        "owner_id": None,
    }
    assert client.configs.integrations.find_one({"name": "notion"})["configs"] == {
        "enabled": False
    }
    assert client.migrations.lock.count_documents({}) == 0, "the lock is released"


def test_the_ledger_names_each_file_by_its_folder_and_keeps_its_counts(
    client, versions
):
    run(client, versions)

    schema, data = (
        client.migrations.changelog.find_one({"_id": entry_id})
        for entry_id in ("schema/0001", "data/0001")
    )

    assert {field for field in data} == {
        "_id",
        "kind",
        "number",
        "name",
        "sha256",
        "applied_at",
        "seconds",
        "counts",
        "app_version",
    }
    assert (schema["kind"], schema["number"], schema["name"]) == ("schema", 1, "baseline")
    assert schema["counts"] == {"collections_created": 31, "indexes_created": 25}
    assert (data["kind"], data["number"], data["name"]) == ("data", 1, "bot_defaults")
    assert data["counts"] == {"configs_data_created": 1, "configs_notion_created": 1}
    assert data["sha256"] == next(
        found.sha256 for found in versions if found.id == "data/0001"
    )


def test_a_second_run_applies_nothing_and_changes_no_count(client, versions):
    run(client, versions)
    before_counts, before_ledger = counts(client), ledger(client)

    passed, out = run(client, versions)

    assert passed, out
    assert "nothing to apply" in out and "apply " not in out
    assert counts(client) == before_counts
    assert ledger(client) == before_ledger


def test_every_pending_schema_file_runs_before_the_first_data_file(client, versions):
    seen = []

    def fill(db):
        seen.append(sorted(db.client.guild.ordered.index_information()))
        with begin(db) as tx:
            tx.guild.ordered.insert_one({"code": "a"})
            tx.count("inserted")

    schema_number, data_number = next_number(versions, "schema"), next_number(versions, "data")
    files = [
        *versions,
        data_file(data_number, "fill", fill),
        schema_file(
            schema_number, "ordered", {"guild": {"ordered": [index("code", unique=True)]}}
        ),
    ]

    passed, out = run(client, files)

    assert passed, out
    assert seen == [["_id_", "code_1"]]
    applied = [
        line.split(":")[0].split()[1] for line in out.splitlines() if line.startswith("apply")
    ]
    assert applied == sorted(applied, key=lambda label: (label.startswith("data"), label))
    assert applied[-1] == f"data/{data_number:04d}_fill"
    assert f"schema/{schema_number:04d}_ordered" in applied


def test_a_failing_data_file_leaves_no_ledger_entry_and_none_of_its_writes(
    client, versions
):
    def broken(db):
        with begin(db) as tx:
            tx.guild.moderations.insert_one({"guild_id": "half-written"})
            raise RuntimeError("the step broke")

    number = next_number(versions, "data")
    passed, out = run(client, [*versions, data_file(number, "broken", broken)])

    assert not passed
    assert f"data/{number:04d}_broken not applied: RuntimeError: the step broke" in out
    assert ledger_ids(client) == shipped_ids(versions)
    assert client.guild.moderations.count_documents({"guild_id": "half-written"}) == 0

    def fixed(db):
        with begin(db) as tx:
            tx.guild.moderations.insert_one({"guild_id": "whole"})

    passed, out = run(client, [*versions, data_file(number, "broken", fixed)])

    assert passed, out
    assert ledger_ids(client) == sorted([*shipped_ids(versions), f"data/{number:04d}"])
    assert client.guild.moderations.count_documents({"guild_id": "whole"}) == 1


def test_a_data_file_that_never_enters_begin_is_not_recorded(client, versions):
    number = next_number(versions, "data")
    passed, out = run(client, [*versions, data_file(number, "lazy", lambda db: None)])

    assert not passed
    assert f"data/{number:04d}_lazy not applied: TransactionMisuse" in out
    assert f"data/{number:04d}" not in ledger_ids(client)


def test_a_data_file_over_the_transaction_budget_writes_nothing(
    client, versions, monkeypatch
):
    monkeypatch.setattr(Migrations, "TRANSACTION_BUDGET_SECONDS", -1.0)

    passed, out = run(client, versions)

    assert not passed
    assert "BudgetExceeded" in out
    assert ledger_ids(client) == shipped_ids(versions, "schema"), (
        "the schema before it stays applied"
    )
    assert client.configs.data.count_documents({}) == 0


def test_a_second_runner_stops_on_the_lock_naming_the_holder(client, versions):
    runner.prepare_ledger(client)
    runner.take_lock(client, "deploy-runner:42")

    passed, out = run(client, versions)

    assert not passed
    assert "lock held by deploy-runner:42" in out
    assert ledger(client) == []
    assert "guild" not in databases(client)
    assert client.migrations.lock.find_one()["holder"] == "deploy-runner:42"


def test_two_runners_at_once_take_the_lock_once(client):
    runner.prepare_ledger(client)
    taken, refused = [], []
    start = threading.Barrier(2)

    def take(who):
        start.wait()
        try:
            runner.take_lock(client, who)
            taken.append(who)
        except runner.LockHeld:
            refused.append(who)

    threads = [threading.Thread(target=take, args=(who,)) for who in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(taken) == 1 and len(refused) == 1


def test_an_expired_lock_is_taken_over(client):
    runner.prepare_ledger(client)
    client.migrations.lock.insert_one(
        {
            "_id": "lock",
            "holder": "killed:1",
            "expires_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
        }
    )

    runner.take_lock(client, "next:2")

    assert client.migrations.lock.find_one()["holder"] == "next:2"


def test_a_dry_run_writes_nothing_but_reports_real_counts(client, versions):
    client.guild.moderations.insert_many([{"guild_id": "1"}, {"guild_id": "2"}])
    client.configs.data.insert_one({"status": "online", "prefix": "ks!"})
    before = counts(client)

    passed, out = run(client, versions, dry_run=True)

    assert passed, out
    assert (
        'dry     data/0001_bot_defaults would write {"configs_data_created": 0,'
        ' "configs_notion_created": 1}'
    ) in out
    assert "schema/0001_baseline would create collection guild.logs" in out
    assert "would create index guild.logs ttl(ts, days=30)" in out
    assert counts(client) == before
    assert databases(client) == {"guild", "configs"}


def test_verify_passes_after_a_run_and_fails_once_an_index_is_dropped(client, versions):
    run(client, versions)
    expected = runner.expected_structure(versions)

    out = io.StringIO()
    assert runner.verify(client, expected, runner.Report(out)), out.getvalue()

    client.guild.logs.drop_index("ts_1")
    client.guild.extra.insert_one({})
    out = io.StringIO()

    assert not runner.verify(client, expected, runner.Report(out))
    assert "fail    missing index guild.logs ts_1 ttl(ts, days=30)" in out.getvalue()
    assert "extra   collection guild.extra" in out.getvalue()


def test_an_index_with_the_same_name_and_another_spec_stops_the_run_before_writing(
    client, versions
):
    client.guild.logs.create_index([("ts", 1)], name="ts_1", expireAfterSeconds=60)

    passed, out = run(client, versions)

    assert not passed
    assert (
        'index guild.logs ts_1 exists as {"expireAfterSeconds": 60, "key": [["ts", 1]]},'
        ' this code declares {"expireAfterSeconds": 2592000, "key": [["ts", 1]]}'
    ) in out
    assert ledger(client) == []
    assert databases(client) == {"guild", "migrations"}
    assert list(client.guild.list_collection_names()) == ["logs"]


def test_duplicate_guilds_stop_the_run_before_writing_naming_the_guild(
    client, versions
):
    client.guild.moderations.insert_many([{"guild_id": "4242"}, {"guild_id": "4242"}])

    passed, out = run(client, versions)

    assert not passed
    assert 'guild.moderations: 2 documents share guild_id = ["4242"]' in out
    assert ledger(client) == []
    assert client.guild.list_collection_names() == ["moderations"]


def test_a_second_bot_document_stops_the_run_before_writing(client, versions):
    client.configs.data.insert_many([{"status": "online"}, {"status": "idle"}])

    passed, out = run(client, versions)

    assert not passed
    assert (
        "check data/0001_bot_defaults: configs.data holds 2 bot documents, expected one"
    ) in out
    assert ledger(client) == []
    assert "guild" not in databases(client)


def test_the_boot_check_passes_only_after_the_run(client, versions):
    with pytest.raises(runner.MigrationsPending):
        runner.require_applied(client, versions)

    run(client, versions)

    runner.require_applied(client, versions)


def test_the_cli_runs_status_migrate_verify_and_snapshot(client, versions):
    out = io.StringIO()

    assert main(["migrate", "--dry-run"], client=client, out=out) == 0
    assert main(["migrate"], client=client, out=out) == 0
    assert main(["status"], client=client, out=out) == 0
    assert main(["verify"], client=client, out=out) == 0
    snapshot = io.StringIO()
    assert main(["snapshot"], client=client, out=snapshot) == 0

    schema_files = len(shipped_ids(versions, "schema"))
    assert f"info    schema/: {schema_files} applied, 0 pending" in out.getvalue()
    assert "applied data/0001_bot_defaults" in out.getvalue()
    printed = json.loads(snapshot.getvalue())
    assert printed.pop(f"{MARKER}.owner") == []
    assert printed == json.loads(runner.SNAPSHOT.read_text())
    client.guild.logs.drop_index("ts_1")
    assert main(["verify"], client=client, out=io.StringIO()) == 1


def test_from_empty_keiko_gets_its_settings_and_no_server_document(client, versions):
    passed, out = run(client, versions)

    assert passed, out
    assert client.guild.settings.count_documents({}) == 0
    assert client.guild.cogs.count_documents({}) == 0
    assert client.keiko.settings.find_one({}) == {
        "_id": "keiko",
        "options": {
            "presence": {"status": "online", "activity": 0, "description": "/help"},
            "prefix": "ke!",
            "owner_id": None,
            "operator": {},
        },
    }
    assert client.keiko.integrations.find_one({}) == {
        "_id": "notion",
        "options": {"enabled": False},
    }


JOINED = datetime(2025, 3, 1)
LEFT = datetime(2026, 2, 1)
FLAGS = {
    "welcome_messages": True,
    "default_roles": True,
    "block_links": False,
    "notifications_twitch": True,
    "notifications_youtube_video": False,
    "reminders_birthday": True,
    "stream_elements_commands": True,
}


def production_shaped(client):
    """Production before v1: flags on the moderations documents and no settings."""
    client.configs.data.insert_one(
        {"prefix": "ke!", "status": "idle", "activity": 2, "description": "/help",
         "owner_id": None}
    )
    client.configs.admin.insert_one(
        {"admin_guild_id": "123", "admin_logs_channel_id": "456"}
    )
    client.configs.integrations.insert_one(
        {"name": "notion", "configs": {"enabled": True, "database_id": "db"}}
    )
    client.guild.moderations.insert_many([
        {"guild_id": "1001", "owner_id": "9", "is_bot_online": True, **FLAGS,
         "created_at": JOINED, "updated_at": JOINED},
        {"guild_id": "1002", "owner_id": "9", "is_bot_online": False, **FLAGS,
         "created_at": JOINED, "updated_at": LEFT},
    ])
    guild = client.guild
    guild.welcome_messages.insert_one(
        {"guild_id": "1001", "enabled": True, "welcome_messages_title": "Hi"}
    )
    guild.default_roles.insert_one(
        {"guild_id": "1001", "default_roles": {"style": "role", "values": ["7"]}}
    )
    guild.block_links.insert_one({"guild_id": "1001", "enabled": False, "mode": "block_all"})
    guild.notifications_twitch.insert_one({
        "guild_id": "1001",
        "enabled": True,
        "notifications": {"style": "composition", "values": [{"streamer": {"value": "x"}}]},
    })
    guild.notifications_youtube_video.insert_one({"guild_id": "1001", "enabled": True})
    guild.reminders_birthday.insert_one({
        "guild_id": "1001",
        "channel_id": "400",
        "mention_everyone": False,
        "locale": "en-us",
        "timezone": "Europe/Lisbon",
        "notification_time": "09:00",
    })
    guild.stream_elements_commands.insert_one(
        {"guild_id": "1001", "enabled": True, "streamer": "shroud", "channel_id": "5f"}
    )


def test_a_production_shaped_database_moves_to_the_new_collections(client, versions):
    production_shaped(client)

    passed, out = run(client, versions)

    assert passed, out
    present = client.guild.settings.find_one({"_id": "1001"})
    assert present["options"] == {
        "language": "pt-br",
        "timezone": "Europe/Lisbon",
        "prefix": "ke!",
    }
    assert present["state"] == {
        "owner_id": "9", "present": True, "joined_at": JOINED, "left_at": None
    }
    absent = client.guild.settings.find_one({"_id": "1002"})
    assert absent["state"]["present"] is False and absent["state"]["left_at"] == LEFT

    cogs = {document["cog"]: document for document in client.guild.cogs.find()}
    assert len(cogs) == 7
    assert {cog: document["_id"] for cog, document in cogs.items()} == {
        cog: f"1001:{cog}" for cog in FLAGS
    }
    assert cogs["default_roles"]["enabled"] is True, "no enabled: the old flag"
    assert cogs["block_links"]["enabled"] is False
    assert cogs["stream_elements_commands"]["state"] == {"channel_id": "5f"}
    assert cogs["stream_elements_commands"]["options"] == {"streamer": "shroud"}
    assert cogs["reminders_birthday"]["options"] == {
        "channel": "400",
        "mention_everyone": False,
        "notification_time": "09:00",
    }

    keiko = client.keiko.settings.find_one({"_id": "keiko"})["options"]
    assert keiko["presence"] == {"status": "idle", "activity": 2, "description": "/help"}
    assert keiko["operator"] == {"admin_guild_id": 123, "admin_logs_channel_id": 456}
    assert client.keiko.integrations.find_one({"_id": "notion"})["options"] == {
        "enabled": True, "database_id": "db"
    }

    entries = {entry["_id"]: entry["counts"] for entry in ledger(client)}
    assert entries["data/0002"] == {"settings_created": 2}
    assert entries["data/0003"] == {
        "cogs_copied": 7, "keiko_settings_created": 1, "integrations_copied": 1
    }
    assert client.guild.moderations.count_documents({}) == 2, "the old ones stay"

    before = counts(client)
    passed, out = run(client, versions)
    assert passed and "nothing to apply" in out
    assert counts(client) == before


def test_a_feature_document_whose_server_id_is_not_text_stops_the_run(client, versions):
    client.guild.welcome_messages.insert_one({"guild_id": 1001, "enabled": True})

    passed, out = run(client, versions)

    assert not passed
    assert "has a guild_id that is not text: 1001" in out
    assert "keiko" not in databases(client)


def test_two_integrations_with_the_same_name_stop_the_run_before_writing(
    client, versions
):
    client.configs.integrations.insert_many([
        {"name": "notion", "configs": {"enabled": True}},
        {"name": "notion", "configs": {"enabled": False}},
    ])

    passed, out = run(client, versions)

    assert not passed
    assert "configs.integrations holds 2 documents named 'notion'" in out
    assert "keiko" not in databases(client)


def test_a_birthday_config_key_the_file_would_drop_stops_the_run(client, versions):
    client.guild.reminders_birthday.insert_one(
        {"guild_id": "1001", "channel_id": "400", "role_id": "77", "locale": "en-us"}
    )

    passed, out = run(client, versions)

    assert not passed
    assert "guild.reminders_birthday" in out
    assert "holds role_id, which this file would drop" in out
    assert "keiko" not in databases(client)


def test_an_operator_id_that_is_not_a_number_stops_the_run(client, versions):
    client.configs.data.insert_one({"status": "online", "prefix": "ke!"})
    client.configs.admin.insert_one({"admin_guild_id": "the main one"})

    passed, out = run(client, versions)

    assert not passed
    assert "configs.admin admin_guild_id is 'the main one', not a Discord id" in out

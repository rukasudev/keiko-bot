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


def test_from_empty_to_the_latest_files_the_database_is_the_snapshot(
    client, versions
):
    passed, out = run(client, versions)

    assert passed, out
    assert ledger_ids(client) == ["data/0001", "schema/0001"]
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
    assert data["sha256"] == versions[1].sha256


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

    files = [
        *versions,
        data_file(2, "fill", fill),
        schema_file(2, "ordered", {"guild": {"ordered": [index("code", unique=True)]}}),
    ]

    passed, out = run(client, files)

    assert passed, out
    assert seen == [["_id_", "code_1"]]
    applied = [line.split(":")[0] for line in out.splitlines() if line.startswith("apply")]
    assert applied == [
        "apply   schema/0001_baseline",
        "apply   schema/0002_ordered",
        "apply   data/0001_bot_defaults",
        "apply   data/0002_fill",
    ]


def test_a_failing_data_file_leaves_no_ledger_entry_and_none_of_its_writes(
    client, versions
):
    def broken(db):
        with begin(db) as tx:
            tx.guild.moderations.insert_one({"guild_id": "half-written"})
            raise RuntimeError("the step broke")

    passed, out = run(client, [*versions, data_file(2, "broken", broken)])

    assert not passed
    assert "data/0002_broken not applied: RuntimeError: the step broke" in out
    assert ledger_ids(client) == ["data/0001", "schema/0001"]
    assert client.guild.moderations.count_documents({"guild_id": "half-written"}) == 0

    def fixed(db):
        with begin(db) as tx:
            tx.guild.moderations.insert_one({"guild_id": "whole"})

    passed, out = run(client, [*versions, data_file(2, "broken", fixed)])

    assert passed, out
    assert ledger_ids(client) == ["data/0001", "data/0002", "schema/0001"]
    assert client.guild.moderations.count_documents({"guild_id": "whole"}) == 1


def test_a_data_file_that_never_enters_begin_is_not_recorded(client, versions):
    passed, out = run(client, [*versions, data_file(2, "lazy", lambda db: None)])

    assert not passed
    assert "data/0002_lazy not applied: TransactionMisuse" in out
    assert "data/0002" not in ledger_ids(client)


def test_a_data_file_over_the_transaction_budget_writes_nothing(
    client, versions, monkeypatch
):
    monkeypatch.setattr(Migrations, "TRANSACTION_BUDGET_SECONDS", -1.0)

    passed, out = run(client, versions)

    assert not passed
    assert "BudgetExceeded" in out
    assert ledger_ids(client) == ["schema/0001"], "the schema before it stays applied"
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


def test_the_cli_runs_status_migrate_verify_and_snapshot(client):
    out = io.StringIO()

    assert main(["migrate", "--dry-run"], client=client, out=out) == 0
    assert main(["migrate"], client=client, out=out) == 0
    assert main(["status"], client=client, out=out) == 0
    assert main(["verify"], client=client, out=out) == 0
    snapshot = io.StringIO()
    assert main(["snapshot"], client=client, out=snapshot) == 0

    assert "info    schema/: 1 applied, 0 pending" in out.getvalue()
    assert "applied data/0001_bot_defaults" in out.getvalue()
    printed = json.loads(snapshot.getvalue())
    assert printed.pop(f"{MARKER}.owner") == []
    assert printed == json.loads(runner.SNAPSHOT.read_text())
    client.guild.logs.drop_index("ts_1")
    assert main(["verify"], client=client, out=io.StringIO()) == 1

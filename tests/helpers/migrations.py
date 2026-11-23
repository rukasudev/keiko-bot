"""A data migration run in a test: offline over the Mongo fake, its counts and notes
kept the way the runner's dry run prints them, or for real on a throwaway replica set
(MONGO_TEST_URL), like the runner's own tests."""

import io
import os
from contextlib import contextmanager

import pytest

from app.constants import Migrations
from app.migrations import runner

MARKER = "keiko_migrations_test"


class OfflineTransaction:
    """The `tx` a data file writes through, over the Mongo fake, counting and listing
    as the runner does."""

    def __init__(self, client):
        self._client = client
        self.counts = {}
        self.notes = {}

    def __getattr__(self, database):
        return self._client[database]

    def count(self, name, amount=1):
        self.counts[name] = self.counts.get(name, 0) + int(amount)

    def note(self, name, value):
        listed = self.notes.setdefault(name, [])
        if str(value) not in listed:
            listed.append(str(value))


def run_offline(monkeypatch, migration):
    """Every run of `migration`'s `up` from now on, each kept as the transaction it
    wrote through once its block ended (a block that raised is not kept)."""
    runs = []

    @contextmanager
    def begin(client):
        transaction = OfflineTransaction(client)
        yield transaction
        runs.append(transaction)

    monkeypatch.setattr(migration, "begin", begin)
    return runs


def throwaway_mongo(suite):
    """A client of the throwaway Mongo of MONGO_TEST_URL, emptied for `suite`; the
    test is skipped without one and the run stops on a server holding real data."""
    if not os.getenv("MONGO_TEST_URL"):
        pytest.skip("MONGO_TEST_URL names no throwaway Mongo")
    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGO_TEST_URL"], serverSelectionTimeoutMS=5000)
    names = set(client.list_database_names()) - Migrations.SYSTEM_DATABASES
    if names and MARKER not in names:
        pytest.exit(
            f"MONGO_TEST_URL holds {sorted(names)} and no {MARKER}: not a throwaway server",
            returncode=3,
        )
    client[MARKER].owner.replace_one(
        {"_id": "owner"}, {"_id": "owner", "suite": suite}, upsert=True
    )
    for name in set(client.list_database_names()) - Migrations.SYSTEM_DATABASES - {MARKER}:
        client.drop_database(name)
    return client


def migrate(client, label):
    """Apply every shipped migration the ledger lacks; what the file `label`
    (`data/0008`) counted."""
    versions, problems = runner.read_versions()
    out = io.StringIO()
    assert not problems and runner.migrate(client, versions, runner.Report(out)), (
        out.getvalue()
    )
    return client.migrations.changelog.find_one({"_id": label})["counts"]


def forget_from(client, label):
    """Take the data file `label` (`data/0008`) and every later data file out of the
    ledger, so the next run applies them again in order, never below one still
    applied."""
    client.migrations.changelog.delete_many({"_id": {"$gte": label, "$lt": "data/~"}})

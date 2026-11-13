"""data/0004 copies the birthdays and the reminder rows into guild.members and keiko.schedules.

Guaranteed: each reminders.birthdays row becomes one member whose `celebrated_year` is its
`state.reminders_birthday.last_celebrated_year` exactly; duplicate rows of one member merge into the newest,
keeping the latest year celebrated; each server and date gets one schedule with the first
reminder id any of its rows carried, the others counted; each YouTube renewal row becomes
one schedule per youtuber with its id as text and its `expires_at` kept, or absent as it
was; `check` refuses a row it cannot copy; a run from empty writes nothing, and a second
run over the same rows writes nothing new.

The mongo half runs against a throwaway replica set (MONGO_TEST_URL), like the runner's.
"""

import importlib
import io
import os
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.constants import Migrations
from app.migrations import runner

MIGRATION = importlib.import_module("app.migrations.data.0004_members_and_schedules")
MARKER = "keiko_migrations_test"
CREATED = datetime(2026, 5, 1, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 12, 1, tzinfo=timezone.utc)


def birthday(guild_id, user_id, date, reminder_id=None, **fields):
    return {
        "_id": ObjectId(),
        "guild_id": guild_id,
        "user_id": user_id,
        "date": date,
        "month": int(date[:2]),
        "day": int(date[3:]),
        "reminder_id": reminder_id,
        "self_edit_count": 0,
        "message": {"mode": "default", "title": None, "content": None},
        "image": {"mode": "default", "url": None},
        "created_at": CREATED,
        "updated_at": CREATED,
        **fields,
    }


def renewal(value, reminder_id, **fields):
    return {
        "_id": ObjectId(),
        "reminder_id": reminder_id,
        "title": "youtube_notification",
        "value": value,
        "created_at": CREATED,
        **fields,
    }


def test_a_birthday_row_becomes_a_member_with_its_year_as_the_last_celebrated():
    row = birthday("1", "7", "05-12", "4001", celebrated_year=2025)

    member = MIGRATION.member_document(row)

    assert member == {
        "_id": "1:7",
        "guild_id": "1",
        "user_id": "7",
        "options": {"reminders_birthday": {
            "date": "05-12",
            "month": 5,
            "day": 12,
            "message": row["message"],
            "image": row["image"],
            "self_edit_count": 0,
        }},
        "state": {"reminders_birthday": {"last_celebrated_year": 2025}},
        "created_at": CREATED,
        "updated_at": CREATED,
    }


def test_a_row_never_celebrated_has_no_year_and_a_null_year_stays_null():
    never = MIGRATION.member_document(birthday("1", "7", "05-12"))
    null = MIGRATION.member_document(birthday("1", "8", "05-12", celebrated_year=None))

    assert never["state"] == {}
    assert null["state"] == {"reminders_birthday": {"last_celebrated_year": None}}


def test_duplicate_rows_of_one_member_merge_into_the_newest_keeping_the_latest_year():
    rows = [
        birthday("1", "7", "05-12", celebrated_year=2026),
        birthday("1", "7", "06-01", celebrated_year=2024),
    ]

    members, duplicates = MIGRATION.members_from(rows)

    assert duplicates == 1
    [member] = members
    assert member["options"]["reminders_birthday"]["date"] == "06-01"
    assert member["state"]["reminders_birthday"]["last_celebrated_year"] == 2026


def test_one_schedule_per_server_and_date_keeps_the_first_reminder_and_counts_the_rest():
    rows = [
        birthday("1", "7", "05-12", None),
        birthday("1", "8", "05-12", "4001"),
        birthday("1", "9", "05-12", "4002"),
        birthday("2", "7", "05-12", "4001"),
        birthday("2", "8", "07-01"),
    ]
    members, _ = MIGRATION.members_from(rows)

    schedules, extras = MIGRATION.birthday_schedules(rows, members)

    assert [(schedule["_id"], schedule["reminder_id"]) for schedule in schedules] == [
        ("birthday:1:05-12", "4001"),
        ("birthday:2:05-12", "4001"),
        ("birthday:2:07-01", None),
    ]
    assert extras == 1
    assert all(
        schedule["kind"] == "birthday" and schedule["due_at"] is None
        for schedule in schedules
    )


def test_a_renewal_row_becomes_a_schedule_with_its_id_as_text():
    rows = [
        renewal("pewdiepie", 7001, expires_at=EXPIRES),
        renewal("pewdiepie", "7002"),
        renewal("mrbeast", "7003"),
        renewal("ghost", None),
    ]

    schedules, extras, without = MIGRATION.renewal_schedules(rows)

    assert schedules == [
        {
            "_id": "youtube_renewal:pewdiepie",
            "kind": "youtube_renewal",
            "reminder_id": "7001",
            "due_at": None,
            "created_at": CREATED,
            "expires_at": EXPIRES,
        },
        {
            "_id": "youtube_renewal:mrbeast",
            "kind": "youtube_renewal",
            "reminder_id": "7003",
            "due_at": None,
            "created_at": CREATED,
        },
    ]
    assert (extras, without) == (1, 1)


def test_the_check_refuses_what_it_cannot_copy():
    problems = MIGRATION.problems_in(
        [
            {"_id": 1, "guild_id": "1", "user_id": "7", "date": "02-30"},
            {"_id": 2, "guild_id": None, "user_id": "7", "date": "05-12"},
            {"_id": 3, "guild_id": "1", "user_id": "8", "date": "02-29"},
        ],
        [
            {"_id": 4, "title": "something_else", "value": "x", "reminder_id": "1"},
            {"_id": 5, "title": "youtube_notification", "value": "", "reminder_id": "2"},
            {"_id": 6, "title": "youtube_notification", "value": "a", "reminder_id": "3"},
        ],
    )

    assert problems == [
        "reminders.birthdays 1 has the date '02-30'",
        "reminders.birthdays 2 names no server or user",
        "audit.reminders 4 has the title 'something_else'",
        "audit.reminders 5 names no youtuber",
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
    return client.migrations.changelog.find_one({"_id": "data/0004"})["counts"]


def production_shaped(client):
    client.reminders.birthdays.insert_many([
        birthday("1", "7", "05-12", "4001", celebrated_year=2025),
        birthday("1", "8", "05-12", "4002"),
        birthday("2", "7", "05-12", "4001"),
        birthday("2", "9", "11-30", None),
        birthday("2", "9", "11-30", None, celebrated_year=2026),
    ])
    client.audit.reminders.insert_many([
        renewal("pewdiepie", 7001),
        renewal("mrbeast", "7002", expires_at=EXPIRES, hub_confirmed_at=CREATED),
    ])


@pytest.mark.mongo
def test_from_empty_the_file_writes_nothing(server):
    counts = migrate(server)

    assert counts == {
        "members_created": 0,
        "birthday_rows_merged": 0,
        "birthday_schedules_created": 0,
        "birthday_schedules_without_reminder": 0,
        "birthday_reminders_left_out": 0,
        "renewal_schedules_created": 0,
        "renewal_rows_left_out": 0,
        "renewal_rows_without_reminder": 0,
    }
    assert server.guild.members.count_documents({}) == 0
    assert server.keiko.schedules.count_documents({}) == 0


@pytest.mark.mongo
def test_production_rows_land_in_their_new_homes(server):
    production_shaped(server)

    counts = migrate(server)

    assert counts["members_created"] == 4
    assert counts["birthday_rows_merged"] == 1
    assert counts["birthday_schedules_created"] == 3
    assert counts["birthday_reminders_left_out"] == 1
    assert counts["birthday_schedules_without_reminder"] == 1
    assert counts["renewal_schedules_created"] == 2
    assert server.guild.members.find_one({"_id": "1:7"})["state"] == {"reminders_birthday": {"last_celebrated_year": 2025}}
    assert server.guild.members.find_one({"_id": "2:9"})["state"] == {"reminders_birthday": {"last_celebrated_year": 2026}}
    schedules = {found["_id"]: found for found in server.keiko.schedules.find({})}
    assert schedules["birthday:1:05-12"]["reminder_id"] == "4001"
    assert schedules["birthday:2:05-12"]["reminder_id"] == "4001"
    assert schedules["birthday:2:11-30"]["reminder_id"] is None
    assert schedules["youtube_renewal:pewdiepie"]["reminder_id"] == "7001"
    assert "expires_at" not in schedules["youtube_renewal:pewdiepie"]
    assert schedules["youtube_renewal:mrbeast"]["expires_at"].replace(
        tzinfo=timezone.utc
    ) == EXPIRES
    assert server.reminders.birthdays.count_documents({}) == 5, "the old rows stay a release"


@pytest.mark.mongo
def test_a_second_run_over_the_same_rows_writes_nothing_new(server):
    production_shaped(server)
    migrate(server)
    server.migrations.changelog.delete_many({"_id": {"$gte": "data/0004", "$lt": "data/~"}})
    server.guild.members.update_one({"_id": "1:7"}, {"$set": {"state.display_name": "Ana"}})

    counts = migrate(server)

    assert counts["members_created"] == 0 and counts["birthday_schedules_created"] == 0
    assert counts["renewal_schedules_created"] == 0
    assert server.guild.members.find_one({"_id": "1:7"})["state"]["display_name"] == "Ana"


@pytest.mark.mongo
def test_a_row_it_cannot_copy_stops_the_run_before_any_write(server):
    server.reminders.birthdays.insert_one(birthday("1", "7", "05-12") | {"date": "13-01"})
    versions, _ = runner.read_versions()
    out = io.StringIO()

    assert not runner.migrate(server, versions, runner.Report(out))
    assert "has the date '13-01'" in out.getvalue()
    assert "guild" not in server.list_database_names() or (
        server.guild.members.count_documents({}) == 0
    )

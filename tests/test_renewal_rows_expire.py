"""A YouTube renewal row lives while its renewal does, and v0.9.0's rows are never touched.

Broke as: `audit.reminders` only grew. Every youtuber a server started to follow
added a row, and v0.9.0 never deleted one: its unsubscribe asked to delete the
row by a field the row does not have, while it did delete the renewal reminder
on reminders-api, so a row whose reminder was gone stayed forever, with nothing
left that would ever read it again.

Shared behaviour: the renewal rows of `app/data/reminder.py` (written when a
server follows a youtuber nobody followed, stamped by every renewal the hub
takes and at every start, read by the reminder webhook) and the indexes of
`app/data/indexes.py`. Consumer that exposed it: notifications_youtube_video
(the review of v1, section 12, "collections without a TTL that only grow").

Guaranteed: a row written now carries `expires_at`, moved forward every time the
hub takes its subscription, and a TTL index removes it once that date has
passed, so only a row nothing has renewed for `YOUTUBE_RENEWAL_ROW_SECONDS` goes;
a row without the field (every row v0.9.0 wrote or writes) never gets one and
never expires. An index may name a collection of another database as
`database.collection`; a database name holds no dot, a collection name may.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import Commands
from app.data import indexes
from app.data.reminder import (
    find_reminder_by_id,
    insert_renewal_reminder,
    stamp_hub_confirmation,
)

pytestmark = pytest.mark.unit

ROW_SECONDS = timedelta(seconds=Commands.YOUTUBE_RENEWAL_ROW_SECONDS)


def test_a_new_renewal_row_expires_once_nothing_has_renewed_it(deps):
    before = datetime.now(timezone.utc)

    insert_renewal_reminder(7001, "pewdiepie")

    row = find_reminder_by_id(7001)
    assert before + ROW_SECONDS <= row["expires_at"] <= datetime.now(timezone.utc) + ROW_SECONDS
    assert ("audit.reminders", [("expires_at", 1)], {"expireAfterSeconds": 0}) in (
        indexes.INDEXES
    )


def test_a_renewal_the_hub_takes_keeps_its_row_alive(deps):
    insert_renewal_reminder(7001, "pewdiepie")
    later = datetime.now(timezone.utc) + timedelta(days=30)

    stamp_hub_confirmation("pewdiepie", later)

    row = find_reminder_by_id(7001)
    assert row["hub_confirmed_at"] == later
    assert row["expires_at"] == later + ROW_SECONDS


def test_a_row_v090_wrote_never_gets_an_expiry(deps):
    deps.mongo_client.audit.reminders.insert_one(
        {"reminder_id": "6001", "title": "youtube_notification", "value": "pewdiepie"}
    )

    stamp_hub_confirmation("pewdiepie", datetime.now(timezone.utc))

    row = find_reminder_by_id(6001)
    assert row["hub_confirmed_at"], "the stamp still reaches the old row"
    assert "expires_at" not in row, "a row without the field must never expire"


def test_the_ttl_index_is_created_in_the_audit_database():
    created = []

    class Collection:
        def __init__(self, name):
            self.name = name

        def create_index(self, keys, **options):
            created.append((self.name, keys, options))

    class Database:
        def __init__(self, name):
            self.name = name

        def __getitem__(self, collection):
            return Collection(f"{self.name}.{collection}")

    class Client:
        def __getitem__(self, database):
            return Database(database)

    original = indexes.mongo_client
    indexes.mongo_client = Client()
    try:
        indexes.ensure_indexes()
    finally:
        indexes.mongo_client = original

    assert ("audit.reminders", [("expires_at", 1)], {"expireAfterSeconds": 0}) in created
    assert ("guild.blocked_links", [("guild_id", 1), ("created_at", -1)], {}) in created


@pytest.mark.parametrize(
    "named, located",
    [
        ("blocked_links", ("guild", "blocked_links")),
        ("audit.reminders", ("audit", "reminders")),
        ("audit.reminders.archive", ("audit", "reminders.archive")),
    ],
)
def test_an_index_names_its_database_before_the_first_dot(named, located):
    assert indexes.located(named) == located

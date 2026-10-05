"""The monthly events archive and the current size of every guild.

Raw analytics events expire after 90 days and only daily counters remain, so a
funnel (the launch of v1, say) could not be rebuilt once its events were gone,
and a guild's size was recorded only in the event of the day Keiko joined it, so
no profile could tell a large server from a small one. The `Archive` cog posts
each complete month's events once, as gzipped Extended JSON Lines encrypted to an
age public key, on the logs files channel where the daily log export goes, and
writes every guild's current size bucket on its profile once a day.

Broke as (first version, review round 1): the archive went out in plaintext,
with every event's guild and user ids readable in Discord, and the size job
created a profile for every guild Keiko is in, so a guild `/admin forget` had
erased came back the next day and every count of profiles changed meaning.
Lucas's retention rule followed: what names no server and no member is kept for
good, anything that does keeps a TTL, so a file kept forever carries no id.
Broke as (review round 2): the archive kept the stored session id, which the
daily log exports on the same channel carry next to guild and user ids, so one
session joined a line back to its server and member; and only last month was
ever tried, so a month whose every pass failed (the key created after the month
ended, say) was lost while its events were still there.

Guaranteed: each complete month's events, and only them, go out in one file (in
halves of the month while a file is over the channel's upload limit once
encrypted) with one message, readable only with the private key, and come back
with their types; no server, member or other Discord id leaves with them, only
the random event id, the event, its feature, source, result, time and
properties, and a session hashed under a key drawn for each pass and never kept,
so sessions still group inside a file but never match a stored id or another
pass; every complete month the raw events still reach and no pass posted goes
out, oldest first, and one that fails never holds the months after it; without a
valid public key nothing is read or posted and a warning says why; a month is
posted once; a month without events posts nothing; a missing channel posts
nothing and says so; a failing pass is an error that never stops the loop; the
logs tool never takes the archive for a log file; a guild that has a profile
gets its size bucket on it, without losing what the profile holds, and no
profile is ever created; and neither job holds the event loop.
"""
import base64
import gzip
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pyrage
import pytest
from bson import ObjectId, json_util
from pyrage import x25519

from app.cogs import archive as archive_cog
from app.cogs.archive import Archive
from app.data import analytics as analytics_data
from app.services import archive
from app.services.utils import ml
from tests.behavioral.regressions.test_event_loop_is_never_blocked import (
    MIN_TICKS,
    blocking,
    ticks_while,
)
from tests.helpers.documents import keys_in, values_in

pytestmark = pytest.mark.unit

TEN_MB = 10 * 1024 * 1024
IDENTITY = x25519.Identity.generate()
PUBLIC_KEY = str(IDENTITY.to_public())


class Channel:
    def __init__(self, limit=TEN_MB):
        self.guild = SimpleNamespace(filesize_limit=limit)
        self.sends = []

    async def send(self, content=None, file=None):
        self.sends.append((content, file))


def bot_with(channel=None, guilds=(), public_key=PUBLIC_KEY):
    return SimpleNamespace(
        config=SimpleNamespace(
            ADMIN_LOGS_FILES_CHANNEL_ID=7, BACKUP_AGE_PUBLIC_KEY=public_key
        ),
        get_channel=lambda _id: channel,
        guilds=list(guilds),
    )


class Frozen(datetime):
    """The clock of the archive cog, stopped at 2026-10-05 12:00 UTC: August and
    September are complete and their events still inside the 90 days, July is not."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(archive_cog, "datetime", Frozen)


async def run_archive(channel, public_key=PUBLIC_KEY):
    """Drives the cog's task body without starting its loop."""
    bot = bot_with(channel, public_key=public_key)
    return await Archive.archive_months.coro(SimpleNamespace(bot=bot))


def last_month():
    return archive.last_month(Frozen.now(timezone.utc))


def label():
    return last_month().strftime("%Y-%m")


def mark_posted(deps, month_label):
    deps.mongo_client.guild.analytics_archives.insert_one({"_id": month_label})


def posted_months(deps):
    return {marker["_id"] for marker in deps.mongo_client.guild.analytics_archives.find({})}


def store_event(deps, ts, guild_id="1", **extra):
    document = {
        "_id": ObjectId(),
        "event": "feature.setup_opened",
        "event_id": os.urandom(8).hex(),
        "v": 1,
        "ts": ts,
        "guild_id": guild_id,
        "user_id": "9",
        "feature": "block_links",
        "props": {},
        **extra,
    }
    deps.mongo_client.guild.analytics_events.insert_one(document)
    return document


def lines_of(sent):
    """The file opened with the private key and read back the way `mongoimport`
    or a script would, in UTC."""
    options = json_util.RELAXED_JSON_OPTIONS.with_options(tz_aware=True)
    plain = pyrage.decrypt(sent.fp.getvalue(), [IDENTITY])
    with gzip.GzipFile(fileobj=io.BytesIO(plain)) as stream:
        lines = stream.read().decode("utf-8").splitlines()
    return [json_util.loads(line, json_options=options) for line in lines]


def noise(size):
    """Text that does not compress, so a test controls how large a file gets."""
    return base64.b64encode(os.urandom(size)).decode("ascii")


async def test_each_months_events_go_out_in_one_file_with_one_message(deps):
    month = last_month()
    before = store_event(deps, month - timedelta(minutes=1))
    inside = [
        store_event(deps, month + timedelta(days=1)),
        store_event(deps, month + timedelta(days=20), guild_id="2"),
    ]
    store_event(deps, (month + timedelta(days=32)).replace(day=1))
    channel = Channel()

    await run_archive(channel)

    assert [sent.filename for _content, sent in channel.sends] == [
        "keiko_events_2026-08.jsonl.gz.age",
        f"keiko_events_{label()}.jsonl.gz.age",
    ], "October is still running and is not posted"
    (august, first), (content, sent) = channel.sends
    assert [line["event_id"] for line in lines_of(first)] == [before["event_id"]]
    assert august == ml("messages.admin-logs.monthly-events", "en-us").format(
        month="2026-08", count=1
    )
    assert content == ml("messages.admin-logs.monthly-events", "en-us").format(
        month=label(), count=2
    )
    assert [line["event_id"] for line in lines_of(sent)] == [
        event["event_id"] for event in inside
    ]


async def test_the_archive_names_no_server_member_or_discord_id(deps):
    """Read field by field: the ids the archive keeps on purpose (the event's `_id` and
    `event_id`, set here to collide with the raw ids) would fail a check of its text."""
    stored = store_event(
        deps,
        last_month() + timedelta(days=4),
        guild_id="111222333444",
        user_id="555666777888",
        owner_id="999000111222",
        session_id="a1b2c3d4",
        _id=ObjectId("a1b2c3d4" + "0" * 16),
        event_id="111222333444beef",
        source="slash",
        result="success",
        props={
            "step_key": "mode",
            "duration_bucket": "<1m",
            "size_bucket": "50-500",
            "channel_id": "123123123123",
            "message_id": "456456456456",
        },
    )
    channel = Channel()

    await run_archive(channel)

    lines = lines_of(channel.sends[0][1])
    line = lines[0]
    assert {"guild_id", "user_id", "owner_id"} & set(line) == set()
    assert line["props"] == {"step_key": "mode", "duration_bucket": "<1m", "size_bucket": "50-500"}
    assert (line["event_id"], line["event"], line["feature"]) == (
        stored["event_id"], "feature.setup_opened", "block_links"
    )
    assert (line["source"], line["result"]) == ("slash", "success")
    id_fields = {key for key in keys_in(lines) if key.endswith("_id")}
    assert id_fields <= {"_id", "event_id", "session_id"}, f"{id_fields} left in the archive"
    identities = {
        "111222333444", "555666777888", "999000111222", "123123123123", "456456456456",
        "a1b2c3d4",
    }
    identities |= {int(identity) for identity in identities if identity.isdigit()}
    left = [value for value in values_in(lines) if value in identities]
    assert left == [], f"{left} left in the archive"


async def test_no_session_id_ever_stored_reaches_the_archive(deps):
    """The daily log exports carry the same session id next to guild and user ids."""
    month = last_month()
    for day, session in ((1, "sess-raw-one"), (2, "sess-raw-one"), (3, "sess-raw-two")):
        store_event(deps, month + timedelta(days=day), session_id=session)
    channel = Channel()

    await run_archive(channel)

    text = gzip.decompress(pyrage.decrypt(channel.sends[0][1].fp.getvalue(), [IDENTITY]))
    assert b"sess-raw" not in text, "a stored session id reached the archive"
    first, second, third = [line["session_id"] for line in lines_of(channel.sends[0][1])]
    assert first == second != third, "sessions no longer group inside the file"


def test_each_pass_hashes_the_sessions_with_a_key_of_its_own(deps):
    month = last_month()
    store_event(deps, month + timedelta(days=1), session_id="sess-raw-one")
    recipient = IDENTITY.to_public()

    passes = [archive.build_month_files(month, TEN_MB, recipient) for _ in range(2)]

    hashed = [
        lines_of(SimpleNamespace(fp=io.BytesIO(files[0].payload)))[0]["session_id"]
        for files in passes
    ]
    assert hashed[0] != hashed[1], "two passes hashed a session the same way: the key was kept"


async def test_every_unposted_month_still_inside_the_events_life_goes_out_oldest_first(
    deps,
):
    """A month whose every pass failed (the key created after the month boundary, say)."""
    for month in (7, 8, 9):
        store_event(deps, datetime(2026, month, 10, tzinfo=timezone.utc))
    channel = Channel()

    await run_archive(channel)

    posted = [sent.filename for _content, sent in channel.sends]
    assert posted == [
        "keiko_events_2026-08.jsonl.gz.age",
        "keiko_events_2026-09.jsonl.gz.age",
    ], "July started before the events' 90 days and must be left; August was missed"
    assert posted_months(deps) == {"2026-08", "2026-09"}


async def test_a_month_already_posted_is_not_posted_again(deps):
    mark_posted(deps, "2026-08")
    for month in (8, 9):
        store_event(deps, datetime(2026, month, 10, tzinfo=timezone.utc))
    channel = Channel()

    await run_archive(channel)

    assert [sent.filename for _content, sent in channel.sends] == [
        "keiko_events_2026-09.jsonl.gz.age"
    ]


async def test_a_month_that_fails_never_holds_the_months_after_it(
    deps, monkeypatch, caplog
):
    for month in (8, 9):
        store_event(deps, datetime(2026, month, 10, tzinfo=timezone.utc))
    build = archive.build_month_files

    def august_fails(month, *args):
        if month.month == 8:
            raise ConnectionError("mongo is down")
        return build(month, *args)

    monkeypatch.setattr(archive, "build_month_files", august_fails)
    channel = Channel()

    with caplog.at_level(logging.WARNING):
        await run_archive(channel)

    assert [sent.filename for _content, sent in channel.sends] == [
        "keiko_events_2026-09.jsonl.gz.age"
    ]
    assert "Monthly events archive failed for 2026-08: ConnectionError" in caplog.text
    assert posted_months(deps) == {"2026-09"}, "August is tried again on the next pass"


async def test_the_events_come_back_with_their_types(deps):
    stored = store_event(deps, last_month() + timedelta(days=3, hours=4))
    channel = Channel()

    await run_archive(channel)

    restored = lines_of(channel.sends[0][1])[0]
    assert restored["_id"] == stored["_id"]
    assert restored["ts"] == stored["ts"]
    assert restored["v"] == 1


async def test_a_month_is_posted_once_even_when_its_first_day_was_missed(deps):
    store_event(deps, last_month() + timedelta(days=2))
    channel = Channel()

    await run_archive(channel)
    await run_archive(channel)

    assert len(channel.sends) == 1, "the next daily pass posted the month again"
    marker = deps.mongo_client.guild.analytics_archives.find_one({"_id": label()})
    assert marker["events"] == 1 and marker["files"] == 1


async def test_a_month_without_events_posts_nothing_and_is_not_read_again(
    deps, monkeypatch
):
    channel = Channel()

    await run_archive(channel)
    reads = []
    monkeypatch.setattr(
        analytics_data, "iter_events_between", lambda *args: reads.append(args) or []
    )
    await run_archive(channel)

    assert channel.sends == []
    assert reads == []


async def test_a_month_over_the_upload_limit_goes_in_files_under_it(deps):
    month = last_month()
    stored = [
        store_event(deps, month + timedelta(days=day), props={"padding": noise(1500)})
        for day in range(0, 28, 3)
    ]
    channel = Channel(limit=5000)

    await run_archive(channel)

    parts = [part for _content, part in channel.sends]
    assert len(parts) >= 2
    assert all(len(part.fp.getvalue()) <= 5000 for part in parts)
    assert [part.filename for part in parts] == [
        f"keiko_events_{label()}_{index}-of-{len(parts)}.jsonl.gz.age"
        for index in range(1, len(parts) + 1)
    ]
    assert ml("messages.admin-logs.files-parts", "en-us").format(files=len(parts)) in (
        channel.sends[0][0]
    )
    assert [content for content, _part in channel.sends[1:]] == [None] * (len(parts) - 1)
    sent = [line["event_id"] for part in parts for line in lines_of(part)]
    assert sent == [event["event_id"] for event in stored], "every event, once, in order"


async def test_what_is_posted_opens_with_the_private_key_and_no_other(deps):
    stored = store_event(deps, last_month() + timedelta(days=2))
    channel = Channel()

    await run_archive(channel)

    posted = channel.sends[0][1].fp.getvalue()
    assert not posted.startswith(b"\x1f\x8b"), "a readable gzip reached Discord"
    assert stored["event_id"].encode() not in posted
    assert lines_of(channel.sends[0][1])[0]["event_id"] == stored["event_id"]
    with pytest.raises(pyrage.DecryptError):
        pyrage.decrypt(posted, [x25519.Identity.generate()])


@pytest.mark.parametrize("public_key", ["", "not-an-age-key", None])
async def test_without_a_valid_public_key_nothing_is_read_or_posted(
    deps, monkeypatch, caplog, public_key
):
    store_event(deps, last_month() + timedelta(days=2))
    reads = []
    monkeypatch.setattr(
        analytics_data, "iter_events_between", lambda *args: reads.append(args) or []
    )
    channel = Channel()

    with caplog.at_level(logging.WARNING):
        await run_archive(channel, public_key=public_key)

    assert channel.sends == [], "the events went out without being encrypted"
    assert reads == [], "the events were read for a file that could not be encrypted"
    assert "Monthly events archive not posted" in caplog.text
    assert "never posted unencrypted" in caplog.text
    assert deps.mongo_client.guild.analytics_archives.find_one({"_id": label()}) is None


async def test_a_missing_channel_posts_nothing_and_says_so(deps, caplog):
    store_event(deps, last_month() + timedelta(days=2))

    with caplog.at_level(logging.WARNING):
        await run_archive(None)

    assert "Monthly events archive not posted" in caplog.text
    assert deps.mongo_client.guild.analytics_archives.find_one({"_id": label()}) is None


async def test_a_failing_pass_is_an_error_and_never_takes_down_the_loop(
    deps, monkeypatch, caplog
):
    def explode(*_args):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(archive, "build_month_files", explode)

    with caplog.at_level(logging.WARNING):
        assert await run_archive(Channel()) is None

    failures = [
        record for record in caplog.records
        if "Monthly events archive failed" in record.getMessage()
    ]
    assert failures and failures[0].levelno == logging.ERROR


def test_the_logs_tool_never_takes_the_archive_for_a_log_file(deps, monkeypatch):
    from tools.keiko.logs import fetch

    month = last_month()
    for day in range(0, 28, 3):
        store_event(deps, month + timedelta(days=day), props={"padding": noise(1500)})
    recipient = IDENTITY.to_public()
    whole = archive.build_month_files(month, TEN_MB, recipient)
    split = archive.build_month_files(month, 5000, recipient)
    log_name = "keiko_logs_2026-01-01.jsonl.gz"
    attachments = [
        {"filename": part.filename, "url": f"https://cdn.example.com/{index}"}
        for index, part in enumerate([*whole, *split])
    ]
    attachments.append({"filename": log_name, "url": "https://cdn.example.com/log"})
    message = {"id": "1", "attachments": attachments}
    monkeypatch.setattr(fetch, "iter_messages", lambda channel=None: iter([message]))

    picked = [attachment["filename"] for attachment in fetch.iter_log_attachments("1")]

    assert len(split) >= 2
    assert picked == [log_name]


async def test_a_guild_with_a_profile_gets_its_current_size_on_it(deps):
    joined = datetime(2026, 1, 2, tzinfo=timezone.utc)
    profiles = deps.mongo_client.guild.analytics_guild_profile
    profiles.insert_one({"_id": "1", "joined_at": joined, "size_bucket": "5k+"})
    profiles.insert_one({"_id": "2", "joined_at": joined})
    profiles.insert_one({"_id": "3", "joined_at": joined})
    guilds = [
        SimpleNamespace(id=1, member_count=12),
        SimpleNamespace(id=2, member_count=800),
        SimpleNamespace(id=3, member_count=None),
    ]

    await Archive.measure_guild_sizes.coro(SimpleNamespace(bot=bot_with(guilds=guilds)))

    first = profiles.find_one({"_id": "1"})
    assert first["size_bucket"] == "<50", "the current size replaces the one before"
    assert first["joined_at"] == joined, "what the profile held is kept"
    assert isinstance(first["size_measured_at"], datetime)
    assert profiles.find_one({"_id": "2"})["size_bucket"] == "500-5k"
    assert "size_bucket" not in profiles.find_one({"_id": "3"}), "an unknown size is not written"


async def test_measuring_never_creates_a_profile_and_never_undoes_a_forget(deps):
    """A profile is born from a guild's first event; `/admin forget` must stay done."""
    profiles = deps.mongo_client.guild.analytics_guild_profile
    profiles.insert_one({"_id": "1", "joined_at": datetime(2026, 1, 2, tzinfo=timezone.utc)})
    profiles.insert_one({"_id": "2", "joined_at": datetime(2026, 1, 2, tzinfo=timezone.utc)})
    analytics_data.delete_analytics_by_guild("2")
    guilds = [
        SimpleNamespace(id=1, member_count=12),
        SimpleNamespace(id=2, member_count=800),
        SimpleNamespace(id=4, member_count=40),
    ]

    await Archive.measure_guild_sizes.coro(SimpleNamespace(bot=bot_with(guilds=guilds)))

    assert [profile["_id"] for profile in profiles.find({})] == ["1"]


async def test_a_failing_measure_is_a_warning_that_never_takes_down_the_loop(
    deps, monkeypatch, caplog
):
    def explode(*_args):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(analytics_data, "update_profiles", explode)
    guilds = [SimpleNamespace(id=1, member_count=12)]

    with caplog.at_level(logging.WARNING):
        await Archive.measure_guild_sizes.coro(SimpleNamespace(bot=bot_with(guilds=guilds)))

    assert "Guild sizes not recorded: ConnectionError" in caplog.text


async def test_archiving_a_month_never_freezes_the_bot(deps, monkeypatch):
    mark_posted(deps, "2026-08")
    event = {"event": "feature.setup_opened", "ts": last_month(), "props": {}}
    monkeypatch.setattr(analytics_data, "iter_events_between", blocking([event]))
    channel = Channel()

    ticks = await ticks_while(run_archive(channel))

    assert ticks >= MIN_TICKS
    assert len(channel.sends) == 1


async def test_measuring_the_guilds_never_freezes_the_bot(deps, monkeypatch):
    monkeypatch.setattr(analytics_data, "update_profiles", blocking())
    guilds = [SimpleNamespace(id=1, member_count=12)]

    ticks = await ticks_while(
        Archive.measure_guild_sizes.coro(SimpleNamespace(bot=bot_with(guilds=guilds)))
    )

    assert ticks >= MIN_TICKS

"""The cold-tail log reader: parsing both file shapes, and indexing once.

The Discord logs channel is the archive, and it is heterogeneous — years of
plain-text renders before the Mongo sink, gzipped JSON Lines after it. If the
parser drops one of them, that stretch of history is simply gone, because
nothing else kept it.

Idempotence carries equal weight: syncing runs repeatedly against a channel
whose old messages never change, so re-reading a file must add nothing.
"""
import gzip
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.keiko.logs import cli, parse, store  # noqa: E402
from app.constants import DBConfigs

pytestmark = pytest.mark.unit


TEXT_LOG = b"""[INFO] 2026-08-20 10:00:00 - MongoDB: OK
[INFO] 2026-08-20 10:00:01 (guild_id: 921659827523051530) - Command called
[ERROR] 2026-08-20 10:05:00 (interaction_id: 555) - Unexpected error
Traceback (most recent call last):
  File "/app/services/thing.py", line 42, in run
    raise ValueError("the real cause")
ValueError: the real cause
[INFO] 2026-08-20 10:06:00 - back to normal
"""


@pytest.fixture
def connection(tmp_path):
    return store.connect(str(tmp_path / "logs.db"))


# --------------------------------------------------------------------------
# Parsing the legacy text render
# --------------------------------------------------------------------------

def test_text_log_yields_one_record_per_entry_not_per_line():
    records = list(parse.parse_text_log(TEXT_LOG))

    assert len(records) == 4
    assert [record["level"] for record in records] == ["INFO", "INFO", "ERROR", "INFO"]


def test_a_traceback_stays_attached_to_the_entry_that_raised_it():
    """Continuation lines are the traceback; orphaning them loses the cause."""
    error = list(parse.parse_text_log(TEXT_LOG))[2]

    assert error["message"] == "Unexpected error"
    assert "ValueError: the real cause" in error["traceback"]
    assert "line 42, in run" in error["traceback"]


def test_the_optional_context_field_is_read_into_the_right_column():
    records = list(parse.parse_text_log(TEXT_LOG))

    assert records[1]["guild_id"] == "921659827523051530"
    assert records[1]["interaction_id"] is None
    assert records[2]["interaction_id"] == "555"
    assert records[0]["guild_id"] is None


def test_timestamps_become_comparable_iso_strings():
    first = list(parse.parse_text_log(TEXT_LOG))[0]

    assert first["ts"].startswith("2026-08-20T10:00:00")


# --------------------------------------------------------------------------
# Parsing the structured export
# --------------------------------------------------------------------------

def test_the_structured_export_keeps_what_the_text_format_never_had():
    payload = gzip.compress(
        b'\n'.join([
            json.dumps({
                "ts": "2026-08-20T10:00:00+00:00", "level": "ERROR",
                "message": "boom", "session_id": "abc123", "feature": "block_links",
                "traceback": "ValueError: boom", "guild_id": "99",
            }).encode(),
        ])
    )

    record = list(parse.parse_jsonl_gz(payload))[0]

    assert record["session_id"] == "abc123"
    assert record["feature"] == "block_links"
    assert record["guild_id"] == "99"


def test_a_corrupt_line_does_not_cost_the_rest_of_the_file():
    payload = gzip.compress(
        b'{"level": "INFO", "message": "kept"}\nnot json at all\n'
        b'{"level": "INFO", "message": "also kept"}\n'
    )

    records = list(parse.parse_jsonl_gz(payload))

    assert [record["message"] for record in records] == ["kept", "also kept"]


def test_the_extension_decides_the_parser():
    assert len(list(parse.parse_attachment("keiko_log_2023_01_01.log", TEXT_LOG))) == 4
    assert list(parse.parse_attachment(
        "keiko_logs_2026-08-20.jsonl.gz",
        gzip.compress(b'{"message": "structured"}'),
    ))[0]["message"] == "structured"


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------

def test_reindexing_the_same_file_adds_nothing(connection):
    records = list(parse.parse_text_log(TEXT_LOG))

    first = store.insert_entries(connection, records, "keiko_log_2026_08_20.log")
    second = store.insert_entries(connection, records, "keiko_log_2026_08_20.log")

    assert first == 4
    assert second == 0
    assert store.summary(connection)["total"] == 4


def test_two_different_files_with_identical_lines_both_survive(connection):
    records = list(parse.parse_text_log(TEXT_LOG))

    store.insert_entries(connection, records, "day-one.log")
    store.insert_entries(connection, records, "day-two.log")

    assert store.summary(connection)["total"] == 8


def test_a_synced_message_is_remembered_so_the_next_run_can_skip_it(connection):
    assert store.already_synced(connection, "1") is False

    store.mark_synced(connection, "1", filename="keiko_log.log", entries=4)

    assert store.already_synced(connection, "1") is True
    assert store.already_synced(connection, "2") is False


# --------------------------------------------------------------------------
# Querying
# --------------------------------------------------------------------------

def test_full_text_search_reaches_inside_the_traceback(connection):
    store.insert_entries(connection, list(parse.parse_text_log(TEXT_LOG)), "day.log")

    rows = store.query(connection, text="ValueError")

    assert len(rows) == 1
    assert rows[0]["message"] == "Unexpected error"


def test_filters_narrow_by_level_and_guild(connection):
    store.insert_entries(connection, list(parse.parse_text_log(TEXT_LOG)), "day.log")

    assert len(store.query(connection, level="ERROR")) == 1
    assert len(store.query(connection, guild_id="921659827523051530")) == 1


def test_repeated_failures_collapse_into_one_counted_row(connection):
    """300 copies of one stack trace is not 300 findings, and it costs context."""
    records = [
        {"ts": f"2026-08-20T10:00:{index:02d}", "level": "ERROR",
         "message": "Connection refused", "module": "twitch.py"}
        for index in range(30)
    ]
    records.append({
        "ts": "2026-08-20T11:00:00", "level": "ERROR",
        "message": "Something else", "module": "youtube.py",
    })
    store.insert_entries(connection, records, "day.log")

    rows = store.signatures(connection)

    assert len(rows) == 2
    assert rows[0]["occurrences"] == 30
    assert rows[0]["signature"] == "Connection refused"


def test_a_session_id_returns_the_whole_interaction(connection):
    store.insert_entries(connection, [
        {"ts": "2026-08-20T10:00:00", "level": "INFO", "message": "step 1",
         "session_id": "abc"},
        {"ts": "2026-08-20T10:00:05", "level": "INFO", "message": "step 2",
         "session_id": "abc"},
        {"ts": "2026-08-20T10:00:06", "level": "INFO", "message": "elsewhere",
         "session_id": "zzz"},
    ], "day.log")

    rows = store.query(connection, session_id="abc")

    assert len(rows) == 2
    assert {row["message"] for row in rows} == {"step 1", "step 2"}


# --------------------------------------------------------------------------
# Every daily attachment on the channel is named `keiko_log.log`
# --------------------------------------------------------------------------

def day_log(date, message):
    return f"[INFO] {date} 10:00:00 - {message}\n".encode()


def run_sync(tmp_path, monkeypatch, attachments, payloads, **flags):
    from types import SimpleNamespace

    from tools.keiko.logs import cli, fetch

    monkeypatch.setattr(fetch, "iter_log_attachments", lambda: iter(attachments))
    monkeypatch.setattr(fetch, "download", lambda url: payloads[url])

    args = SimpleNamespace(
        db=str(tmp_path / "logs.db"), force=False, incremental=False, limit=0
    )
    for key, value in flags.items():
        setattr(args, key, value)

    cli.command_sync(args)
    return store.connect(args.db)


def test_every_day_is_indexed_even_though_they_all_share_one_filename(
    tmp_path, monkeypatch
):
    """`doRollover` posts `logs/keiko_log.log` every night, so the name repeats.

    Broke as: idempotence keyed on the filename, so a full backfill reported
    success after indexing exactly one of 745 attachments and silently skipping
    the rest. A hole in the archive that announces itself as "done" is worse
    than a crash.
    """
    attachments = [
        {"filename": "keiko_log.log", "url": f"https://cdn/{day}",
         "size": 10, "message_id": str(day)}
        for day in (3, 2, 1)
    ]
    payloads = {
        "https://cdn/3": day_log("2026-08-20", "third day"),
        "https://cdn/2": day_log("2026-08-19", "second day"),
        "https://cdn/1": day_log("2026-08-18", "first day"),
    }

    connection = run_sync(tmp_path, monkeypatch, attachments, payloads)

    summary = store.summary(connection)
    assert summary["total"] == 3, "one entry per day must survive"
    assert summary["files"] == 3

    messages = {row["message"] for row in store.query(connection)}
    assert messages == {"first day", "second day", "third day"}


def test_resyncing_the_same_messages_still_adds_nothing(tmp_path, monkeypatch):
    attachments = [
        {"filename": "keiko_log.log", "url": "https://cdn/1",
         "size": 10, "message_id": "1"},
    ]
    payloads = {"https://cdn/1": day_log("2026-08-18", "only line")}

    run_sync(tmp_path, monkeypatch, attachments, payloads)
    connection = run_sync(tmp_path, monkeypatch, list(attachments), payloads)

    assert store.summary(connection)["total"] == 1


def test_identical_lines_on_different_days_are_both_kept(tmp_path, monkeypatch):
    """Two days can legitimately contain the exact same line."""
    attachments = [
        {"filename": "keiko_log.log", "url": f"https://cdn/{day}",
         "size": 10, "message_id": str(day)}
        for day in (2, 1)
    ]
    payloads = {
        "https://cdn/2": day_log("2026-08-19", "MongoDB: OK"),
        "https://cdn/1": day_log("2026-08-18", "MongoDB: OK"),
    }

    connection = run_sync(tmp_path, monkeypatch, attachments, payloads)

    assert store.summary(connection)["total"] == 2


REFUSED_START = f"{DBConfigs.FATAL_START_PHRASE}: LoginFailure: Improper token has been passed."


@pytest.fixture
def every_level_connection(tmp_path):
    every_level_connection = store.connect(str(tmp_path / "logs.db"))
    store.insert_entries(every_level_connection, [
        {"ts": "2026-10-01T10:00:00", "level": "CRITICAL", "message": REFUSED_START},
        {"ts": "2026-10-01T10:01:00", "level": "ERROR", "message": "Connection refused"},
        {"ts": "2026-10-01T10:02:00", "level": "WARNING", "message": "Slow reply"},
        {"ts": "2026-10-01T10:03:00", "level": "INFO", "message": "All good"},
    ], "day.log")
    return every_level_connection


def signatures(every_level_connection, **options):
    return sorted(row["signature"] for row in store.signatures(every_level_connection, **options))


def test_the_default_report_holds_a_refused_start(every_level_connection):
    """The errors report of the logs tool counts every record at its level and above.

    Reported in the v1 review: `logs errors` grouped only the records whose level was
    exactly ERROR, so the CRITICAL a refused start logs ("Discord refused to start the
    bot", `lifecycle._report`) never appeared in the default report, the one record that
    says the bot is not running at all.

    Shared behaviour: `store.signatures` and the `errors` subcommand of `tools/keiko/logs`.

    Guaranteed: the default report holds ERROR and CRITICAL; `--level WARNING` adds the
    warnings; nothing below the level asked for appears.
    """
    assert signatures(every_level_connection) == sorted([REFUSED_START, "Connection refused"])


def test_a_lower_level_adds_what_is_above_it_too(every_level_connection):
    assert signatures(every_level_connection, level="WARNING") == sorted(
        [REFUSED_START, "Connection refused", "Slow reply"]
    )


def test_the_errors_command_asks_for_error_and_above_by_default(tmp_path, capsys):
    path = str(tmp_path / "logs.db")
    store.insert_entries(store.connect(path), [
        {"ts": "2026-10-01T10:00:00", "level": "CRITICAL", "message": REFUSED_START},
    ], "day.log")

    assert cli.main(["logs", "--db", path, "errors"]) == 0

    assert DBConfigs.FATAL_START_PHRASE in capsys.readouterr().out


def test_a_schema_bump_keeps_the_old_index_aside(tmp_path):
    """Broke as: a new `SCHEMA_VERSION` dropped the whole index, and once the channel keeps
    the daily files 90 days the index is the only copy of the older logs.

    Guaranteed: an index of an older shape is copied to `<path>.v<old version>` before
    the new shape replaces it, with every entry it held.
    """
    path = tmp_path / "logs.db"
    old = sqlite3.connect(str(path))
    old.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, message TEXT)")
    old.execute("INSERT INTO entries (message) VALUES ('a line from 2024')")
    old.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION - 1}")
    old.commit()
    old.close()

    store.connect(str(path))

    kept = sqlite3.connect(str(tmp_path / f"logs.db.v{store.SCHEMA_VERSION - 1}"))
    assert kept.execute("SELECT message FROM entries").fetchall() == [("a line from 2024",)]

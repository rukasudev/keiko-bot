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
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.keiko.logs import parse, store  # noqa: E402

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


def test_a_synced_file_is_remembered_so_the_next_run_can_skip_it(connection):
    assert store.already_synced(connection, "day-one.log") is False

    store.mark_synced(connection, "day-one.log", message_id="1", entries=4)

    assert store.already_synced(connection, "day-one.log") is True


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

"""Contract for the debug-log sink: the hot window of "why did it break?".

Three properties carry the whole design, and each one has cost somewhere else
in this file if it breaks:

- Recording never blocks, never raises, never grows without bound. A command
  must not fail because a log could not be written.
- Persisting never logs. This sink runs underneath `logging`, so a warning
  about a failed write is itself a write — that is a loop, not a message.
- The full traceback survives. It is the reason this exists: the Discord embed
  truncates to the last 15 frames and 3000 characters
  (`format_traceback_message`), and the container deletes the log file.
"""
import gzip
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import Commands as constants
from app.data import indexes
from app.data import logs as logs_data
from app.logger import StoredLogsHandler
from app.services import debug_logs, logs_archive
from app.services.trace import trace_scope

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("debug_logs")]


def make_record(message="something happened", level=logging.INFO, **extra):
    record = logging.LogRecord(
        name="app.test",
        level=level,
        pathname="/app/services/thing.py",
        lineno=42,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def install_handler():
    handler = StoredLogsHandler()
    logging.getLogger().removeHandler(handler)
    return handler


def stored():
    return list(logs_data.mongo_client.guild.logs.find({}))


# --------------------------------------------------------------------------
# Never blocks, never raises, never unbounded
# --------------------------------------------------------------------------

def test_a_full_queue_drops_the_record_instead_of_raising():
    for index in range(constants.DEBUG_LOGS_QUEUE_MAXSIZE + 25):
        accepted = debug_logs.record(debug_logs.build_document(
            level="INFO", message=f"line {index}"
        ))
        assert accepted in (True, False)

    stats = debug_logs.stats()
    assert stats["dropped"] == 25
    assert stats["queue_depth"] == constants.DEBUG_LOGS_QUEUE_MAXSIZE


def test_a_broken_record_never_reaches_the_caller():
    handler = install_handler()

    class Exploding:
        def __str__(self):
            raise ValueError("cannot render")

    handler.emit(make_record(message="%s", args=(Exploding(),)))

    assert debug_logs.stats()["recorded"] == 0


def test_disabling_the_sink_stops_recording():
    debug_logs.configure(type("Config", (), {
        "DEBUG_LOGS_ENABLED": False, "is_prod": lambda self=None: False,
    })())

    assert debug_logs.record({"message": "ignored"}) is False
    assert debug_logs.stats()["recorded"] == 0


# --------------------------------------------------------------------------
# The regression that matters: persisting must never log
# --------------------------------------------------------------------------

def test_a_failing_write_never_produces_another_log(monkeypatch):
    """A logger call here would be recorded, fail, and log again — forever.

    Broke as: the analytics sink reports failures with `logger.warn`. Copying
    that into a sink that *drains the logger* turns one dead connection into an
    unbounded write storm. The failure has to leave the logging tree entirely.
    """
    handler = install_handler()
    logging.getLogger().addHandler(handler)

    def explode(_documents):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(logs_data, "insert_logs", explode)

    debug_logs.record(debug_logs.build_document(level="ERROR", message="boom"))

    reported = []
    debug_logs.flush(on_error=reported.append)

    try:
        assert len(reported) == 1
        assert isinstance(reported[0], ConnectionError)
        # The queue is not refilled by the failure handling itself.
        assert debug_logs.stats()["queue_depth"] == 0
        assert debug_logs.stats()["failed"] == 1
    finally:
        logging.getLogger().removeHandler(handler)


def test_the_failure_reporter_does_not_call_the_logger(capsys):
    from app.cogs.analytics import report_flush_failure

    handler = install_handler()
    logging.getLogger().addHandler(handler)
    try:
        report_flush_failure(ConnectionError("mongo is down"))
    finally:
        logging.getLogger().removeHandler(handler)

    assert "mongo is down" in capsys.readouterr().err
    assert debug_logs.stats()["recorded"] == 0


# --------------------------------------------------------------------------
# What gets stored
# --------------------------------------------------------------------------

def test_the_whole_traceback_is_stored_not_the_truncated_embed_version():
    handler = install_handler()

    try:
        raise ValueError("the real cause")
    except ValueError:
        import sys
        record = make_record(message="failed", level=logging.ERROR)
        record.exc_info = sys.exc_info()
        handler.emit(record)

    debug_logs.flush()
    document = stored()[0]

    assert "ValueError: the real cause" in document["traceback"]
    assert "Traceback (most recent call last)" in document["traceback"]


def test_a_log_written_inside_a_trace_carries_the_session():
    handler = install_handler()

    with trace_scope("block_links", guild_id="99", user_id="7", source="slash"):
        handler.emit(make_record("inside the flow"))

    debug_logs.flush()
    document = stored()[0]

    assert document["guild_id"] == "99"
    assert document["user_id"] == "7"
    assert document["session_id"] is not None
    assert document["message"] == "inside the flow"


def test_record_metadata_points_at_the_code_that_logged():
    handler = install_handler()
    handler.emit(make_record("where am i"))
    debug_logs.flush()

    document = stored()[0]
    assert document["module"] == "thing.py"
    assert document["line"] == 42
    assert document["level"] == "INFO"
    assert document["v"] == debug_logs.SCHEMA_VERSION


def test_an_oversized_message_keeps_its_tail():
    document = debug_logs.build_document(
        level="ERROR", message="x" * (constants.DEBUG_LOGS_MESSAGE_MAX_LENGTH + 500)
    )

    assert len(document["message"]) <= constants.DEBUG_LOGS_MESSAGE_MAX_LENGTH + 2
    assert document["message"].startswith("…")


# --------------------------------------------------------------------------
# Retention: Mongo is a window, Discord is the archive
# --------------------------------------------------------------------------

def test_the_logs_collection_expires_so_mongo_stays_a_window():
    ttl = [
        options["expireAfterSeconds"]
        for collection, _keys, options in indexes.INDEXES
        if collection == "logs" and "expireAfterSeconds" in options
    ]

    assert ttl == [constants.DEBUG_LOGS_TTL_SECONDS]
    assert constants.DEBUG_LOGS_TTL_SECONDS == 60 * 60 * 24 * 30


def test_every_query_the_sink_supports_has_an_index():
    keyed = {
        tuple(key for key, _direction in keys)
        for collection, keys, _options in indexes.INDEXES
        if collection == "logs"
    }

    assert ("level", "ts") in keyed
    assert ("guild_id", "ts") in keyed
    assert ("session_id", "ts") in keyed


# --------------------------------------------------------------------------
# The daily consolidation that becomes the archive
# --------------------------------------------------------------------------

def test_the_daily_export_round_trips_through_gzipped_json_lines():
    day = datetime(2026, 8, 20, tzinfo=timezone.utc)
    logs_data.insert_logs([
        debug_logs.build_document(level="INFO", message="second", ts=day.replace(hour=9)),
        debug_logs.build_document(level="ERROR", message="first", ts=day.replace(hour=1)),
    ])

    payload, written = logs_archive.build_archive(day)

    assert written == 2
    lines = gzip.decompress(payload).decode("utf-8").strip().split("\n")
    records = [json.loads(line) for line in lines]

    assert [record["message"] for record in records] == ["first", "second"]
    assert "_id" not in records[0]
    assert records[0]["ts"].startswith("2026-08-20T01:00")


def test_the_export_only_takes_the_day_it_was_asked_for():
    day = datetime(2026, 8, 20, tzinfo=timezone.utc)
    logs_data.insert_logs([
        debug_logs.build_document(level="INFO", message="today", ts=day.replace(hour=12)),
        debug_logs.build_document(
            level="INFO", message="yesterday", ts=day - timedelta(hours=1)
        ),
        debug_logs.build_document(
            level="INFO", message="tomorrow", ts=day + timedelta(days=1)
        ),
    ])

    payload, written = logs_archive.build_archive(day)

    assert written == 1
    assert "today" in gzip.decompress(payload).decode("utf-8")


def test_an_empty_day_produces_no_file_to_post():
    payload, written = logs_archive.build_archive(
        datetime(2026, 1, 1, tzinfo=timezone.utc)
    )

    assert payload is None
    assert written == 0

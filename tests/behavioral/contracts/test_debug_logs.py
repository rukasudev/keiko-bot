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
from app.constants import LogTypes as logconstants
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


def test_flushing_without_arguments_still_reports_the_failure(monkeypatch, capsys):
    """The guard is the default, not something a caller has to remember.

    Broke as: `flush(on_error=None)` swallowed the exception entirely when the
    argument was omitted, so a second caller would lose write failures in
    silence. Every caller getting it right is not an invariant.
    """
    def explode(_documents):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(logs_data, "insert_logs", explode)
    debug_logs.record(debug_logs.build_document(level="ERROR", message="boom"))

    debug_logs.flush()

    assert "mongo is down" in capsys.readouterr().err
    assert debug_logs.stats()["failed"] == 1


def test_the_failure_reporter_does_not_call_the_logger(capsys):
    handler = install_handler()
    logging.getLogger().addHandler(handler)
    try:
        debug_logs.report_flush_failure(ConnectionError("mongo is down"))
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

    assert len(document["message"]) == constants.DEBUG_LOGS_MESSAGE_MAX_LENGTH
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


# --------------------------------------------------------------------------
# The existing consumer of app/logger.py, with the new handler installed
# --------------------------------------------------------------------------

class _NoopCoroutine:
    def __await__(self):
        yield
        return None

    def close(self):
        return None


class _FakeChannel:
    def __init__(self, sends):
        self._sends = sends

    def send(self, **kwargs):
        self._sends.append(kwargs)
        return _NoopCoroutine()


@pytest.fixture
def both_handlers():
    """Both sinks on the same logger, in the order the bot installs them.

    StoredLogsHandler is created in `LoggerHooks.start()` and DiscordLogsHandler
    by the admin cog, so the stored one always runs first. That ordering matters:
    the embed footer reads `record.asctime`, which only exists once something has
    formatted the record.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app import logger as logger_module
    from app.services import trace as trace_service

    sends = []
    channel = _FakeChannel(sends)
    bot = SimpleNamespace(
        loop=MagicMock(),
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=lambda _channel_id: channel,
    )

    trace_service.clear_sinks()
    stored_handler = StoredLogsHandler()
    discord_handler = logger_module.DiscordLogsHandler(bot)
    discord_handler.schedule_send = lambda coroutine: coroutine.close()

    yield SimpleNamespace(sends=sends, discord=discord_handler, stored=stored_handler)

    logging.getLogger().removeHandler(stored_handler)
    logging.getLogger().removeHandler(discord_handler)
    trace_service.clear_sinks()


def test_the_discord_embed_survives_the_stored_handler_being_installed(both_handlers):
    """Adding a sink to the root logger must not degrade the existing one.

    The embed is the surface a person reads while on call; the stored document
    is the one queried afterwards. Both come off the same record, and nothing
    pinned that they stay independent.
    """
    logging.getLogger().error(
        "twitch subscription failed",
        extra={"guild_id": 4242, "log_type": logconstants.COMMAND_ERROR_TYPE},
    )

    embed = both_handlers.sends[0]["embed"]
    fields = {field.name: field.value for field in embed.fields}

    assert embed.title == logconstants.COMMAND_ERROR_TITLE
    assert fields["Guild ID"] == "4242"
    assert embed.footer.text.startswith("• ")

    debug_logs.flush()
    document = stored()[0]
    assert document["guild_id"] == "4242"
    assert document["message"] == "twitch subscription failed"


def test_both_sinks_read_the_same_identity_from_one_record(both_handlers):
    """One source of truth for ids: the embed and the document cannot disagree.

    Before `record_identity`, each handler derived guild/user/channel on its own,
    so a new field on ErrorContext could reach one and not the other.
    """
    from app.exceptions import ErrorContext

    context = ErrorContext(
        flow="block_links", guild_id="77", user_id="8", channel_id="9"
    )
    logging.getLogger().error("failed in flow", extra={"context": context})

    fields = {f.name: f.value for f in both_handlers.sends[0]["embed"].fields}
    debug_logs.flush()
    document = stored()[0]

    assert fields["Guild Id"] == "77"
    assert document["guild_id"] == "77"
    assert document["user_id"] == "8"
    assert document["channel_id"] == "9"


def test_an_error_still_routes_to_the_error_channel(both_handlers):
    logging.getLogger().error("boom", extra={"guild_id": 1})

    assert len(both_handlers.sends) == 1


# --------------------------------------------------------------------------
# The daily task that turns the hot window into the archive
# --------------------------------------------------------------------------

def run_export(channel, day=None):
    """Drives the cog's task body without starting its loop."""
    import asyncio
    from types import SimpleNamespace

    from app.cogs.analytics import Analytics

    bot = SimpleNamespace(
        config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=7),
        get_channel=lambda _id: channel,
    )
    return asyncio.get_event_loop().run_until_complete(
        Analytics.export_daily_logs.coro(SimpleNamespace(bot=bot))
    )


def test_the_daily_export_posts_yesterday_to_the_files_channel():
    sends = []

    class Channel:
        async def send(self, content, file=None):
            sends.append((content, file))

    yesterday = logs_archive.previous_day()
    logs_data.insert_logs([
        debug_logs.build_document(level="ERROR", message="yesterday's failure", ts=yesterday)
    ])

    run_export(Channel())

    assert len(sends) == 1
    content, sent_file = sends[0]
    assert yesterday.strftime("%Y-%m-%d") in content
    assert sent_file.filename.endswith(".jsonl.gz")


def test_a_quiet_day_posts_nothing_at_all():
    """An empty file every morning is noise that trains people to ignore it."""
    sends = []

    class Channel:
        async def send(self, content, file=None):
            sends.append(content)

    run_export(Channel())

    assert sends == []


def test_a_missing_channel_does_not_raise():
    assert run_export(None) is None


def test_the_export_failing_never_takes_down_the_loop(monkeypatch):
    class Channel:
        async def send(self, content, file=None):
            raise RuntimeError("discord is down")

    logs_data.insert_logs([
        debug_logs.build_document(
            level="INFO", message="something", ts=logs_archive.previous_day()
        )
    ])

    assert run_export(Channel()) is None


# --------------------------------------------------------------------------
# Localization of the one string this feature renders
# --------------------------------------------------------------------------

def test_the_export_message_exists_in_both_locales():
    """Copy lives in YAML, so the pt-br half cannot silently go missing."""
    day = datetime(2026, 8, 20, tzinfo=timezone.utc)

    english = logs_archive.build_message(day, 5, "en-us")
    portuguese = logs_archive.build_message(day, 5, "pt-br")

    assert "2026-08-20" in english and "2026-08-20" in portuguese
    assert "5" in english and "5" in portuguese
    assert english != portuguese
    for rendered in (english, portuguese):
        assert "messages.admin-logs" not in rendered
        assert "—" not in rendered and "–" not in rendered


def test_every_field_the_sink_builds_is_a_column_the_index_stores():
    """A field added to the document but not to the store is dropped in silence."""
    from tools.keiko.logs import store as tools_store

    document = debug_logs.build_document(level="INFO", message="x")
    persisted = set(document) - {"v"}

    assert persisted <= set(tools_store.COLUMNS), (
        f"not indexable: {persisted - set(tools_store.COLUMNS)}"
    )

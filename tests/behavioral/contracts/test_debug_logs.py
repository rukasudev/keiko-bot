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

from app.constants import Commands, Commands as constants
from app.constants import LogTypes as logconstants
from app.data import analytics as analytics_data, indexes
from app.data import logs as logs_data
from app.logger import CustomTimedRotatingFileHandler, LoggerHooks, StoredLogsHandler
from app.services import debug_logs, logs_archive
from app.services.trace import trace_scope
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.services.admin import send_log_file_from_channel_by_date
import os
import pyrage
from pyrage import x25519
from app.config import AppConfig
from tests.test_config import production_config
import base64
import io
import zipfile
from bson import json_util, ObjectId
from app.cogs.analytics import Analytics
from app.services.utils import ml
from app.cogs import analytics as analytics_cog
from tests.behavioral.regressions.test_event_loop_is_never_blocked import (
    blocking,
    MIN_TICKS,
    ticks_while,
)
from tests.helpers.assertions import keys_in, values_in
import discord
from app.services.logs_archive import FILENAME

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


def test_every_stored_line_names_the_release_that_wrote_it(monkeypatch):
    """A line from before a deploy and one from after it read alike without this."""
    monkeypatch.setenv("APP_VERSION", "v1.4.0")
    handler = install_handler()
    handler.emit(make_record("after the deploy"))
    debug_logs.flush()

    assert stored()[0]["app_version"] == "v1.4.0"


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


# --------------------------------------------------------------------------
# The writer thread: durable immediately, batched only when it helps
# --------------------------------------------------------------------------

@pytest.fixture
def writer():
    """The suite never starts it implicitly, so assertions stay deterministic."""
    debug_logs.start_writer()
    yield
    debug_logs.stop_writer()


def wait_for(predicate, timeout=2.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_a_log_reaches_mongo_without_waiting_for_any_tick(writer):
    """The whole point of the writer: no poll interval stands between a line
    being logged and it being queryable.

    Broke as: writes rode the analytics cog's 5-second loop, so a crash inside
    that window lost exactly the records that explained it.
    """
    handler = install_handler()
    handler.emit(make_record("written straight away", level=logging.ERROR))

    assert wait_for(lambda: len(stored()) == 1), "record never reached Mongo"
    assert stored()[0]["message"] == "written straight away"


def test_a_burst_is_written_in_batches_not_one_document_at_a_time(monkeypatch, writer):
    """A busy on_message must not cost one round trip per line."""
    batches = []
    monkeypatch.setattr(logs_data, "insert_logs", lambda docs: batches.append(len(docs)))

    for index in range(600):
        debug_logs.record(debug_logs.build_document(level="INFO", message=f"L{index}"))

    assert wait_for(lambda: sum(batches) == 600), f"only wrote {sum(batches)}"
    assert len(batches) < 600, "every document went out on its own round trip"
    assert max(batches) <= constants.DEBUG_LOGS_FLUSH_BATCH


def test_stopping_the_writer_drains_what_is_still_queued():
    """Shutdown must not drop the last lines before the process goes away."""
    debug_logs.start_writer()
    for index in range(5):
        debug_logs.record(debug_logs.build_document(level="INFO", message=f"L{index}"))

    debug_logs.stop_writer()

    assert len(stored()) == 5


def test_a_failing_write_leaves_the_writer_alive(monkeypatch, capsys, writer):
    """One bad batch must not silently end logging for the rest of the process."""
    monkeypatch.setattr(
        logs_data, "insert_logs",
        lambda docs: (_ for _ in ()).throw(ConnectionError("mongo is down")),
    )
    debug_logs.record(debug_logs.build_document(level="ERROR", message="first"))
    assert wait_for(lambda: debug_logs.stats()["failed"] == 1)

    monkeypatch.undo()
    debug_logs.record(debug_logs.build_document(level="INFO", message="after recovery"))

    assert wait_for(lambda: any(d["message"] == "after recovery" for d in stored()))
    assert debug_logs.writer_running()
    assert "mongo is down" in capsys.readouterr().err


async def test_the_daily_file_is_posted_naming_its_day_without_a_colon(tmp_path):
    """The daily log file's line names its day the way the structured export names its own.

    The structured export and the backup lines drop the colon after "for" in this wave; the
    line the rotating handler posts with the daily file, and the one `/admin logs` answers
    with when it finds that file again, still read "Here is my log file for: **date**!".

    Guaranteed: both read "Here is my log file for **date**!", and `/admin logs` still finds
    the file by its date.
    """
    channel = SimpleNamespace(send=AsyncMock())
    handler = CustomTimedRotatingFileHandler(str(tmp_path / "keiko_log.log"), when="MIDNIGHT")
    handler.bot = SimpleNamespace(
        get_channel=lambda channel_id: channel,
        config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=1),
        loop=asyncio.get_running_loop(),
    )
    handler.emit(logging.makeLogRecord({"msg": "a line of the day"}))

    handler.doRollover()
    await asyncio.sleep(0)
    handler.close()

    line = channel.send.call_args.args[0]
    assert line.startswith(":package: Here is my log file for **")
    assert line.endswith("**!")


async def test_admin_logs_finds_the_file_and_names_its_day_without_a_colon():
    posted = SimpleNamespace(
        content=":package: Here is my log file for **2026-10-01**!",
        attachments=[
            SimpleNamespace(url="https://cdn.discordapp.com/keiko_log.log", filename="keiko_log.log")
        ],
    )

    async def history(limit=None):
        yield posted

    channel = SimpleNamespace(history=history)
    interaction = SimpleNamespace(
        client=SimpleNamespace(
            get_channel=lambda channel_id: channel,
            config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=1),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await send_log_file_from_channel_by_date("2026-10-01", interaction)

    assert interaction.followup.send.await_args.args[0] == (
        ":page_facing_up: Here is my log file for **2026-10-01**! "
        "https://cdn.discordapp.com/keiko_log.log"
    )


def files_channel_answering(*messages):
    async def history(limit=None):
        for message in messages:
            yield message

    return SimpleNamespace(
        client=SimpleNamespace(
            get_channel=lambda channel_id: SimpleNamespace(history=history),
            config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=1),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def test_admin_logs_answers_with_the_daily_log_never_the_backup_of_that_day():
    """`/admin logs <date>` answers with that day's log file, whatever else names the day.

    Broke as: the lookup answered with the first message whose text held the date, and
    the daily backup's message names its date too, so for today it answered with the
    sealed backup's link. Shared behaviour: `send_log_file_from_channel_by_date`
    (`app/services/admin.py`) over the logs files channel the backup and the archive share.
    Guaranteed: only a file named like a daily log is an answer.
    """
    interaction = files_channel_answering(
        SimpleNamespace(
            content=":lock: Here is my encrypted database backup for **2026-10-01**!",
            attachments=[SimpleNamespace(
                url="https://cdn.discordapp.com/keiko_backup_2026-10-01.zip.age",
                filename="keiko_backup_2026-10-01.zip.age",
            )],
        ),
        SimpleNamespace(
            content=":package: Here is my log file for **2026-10-01**!",
            attachments=[SimpleNamespace(
                url="https://cdn.discordapp.com/keiko_logs_2026-10-01.jsonl.gz",
                filename="keiko_logs_2026-10-01.jsonl.gz",
            )],
        ),
    )

    await send_log_file_from_channel_by_date("2026-10-01", interaction)

    assert interaction.followup.send.await_args.args[0].endswith(
        "https://cdn.discordapp.com/keiko_logs_2026-10-01.jsonl.gz"
    )


async def test_admin_logs_skips_a_message_that_carries_no_file():
    """Broke as: a backup summary posted without a file names its date, and the lookup read
    `message.attachments[0]` and raised IndexError instead of reading on."""
    interaction = files_channel_answering(
        SimpleNamespace(
            content=":lock: Here is my encrypted database backup for **2026-10-01**!",
            attachments=[],
        ),
        SimpleNamespace(
            content=":package: Here is my log file for **2026-10-01**!",
            attachments=[SimpleNamespace(
                url="https://cdn.discordapp.com/keiko_log.log", filename="keiko_log.log"
            )],
        ),
    )

    await send_log_file_from_channel_by_date("2026-10-01", interaction)

    assert interaction.followup.send.await_args.args[0].endswith(
        "https://cdn.discordapp.com/keiko_log.log"
    )


TEN_MB = 10 * 1024 * 1024


IDENTITY = x25519.Identity.generate()


PUBLIC_KEY = str(IDENTITY.to_public())


KEY_PARAMETER = "/keiko/backup/age_public_key"


def test_a_sealed_payload_opens_with_the_private_key_and_no_other():
    """Files only the holder of Keiko's age private key can open.

    The monthly events archive and the daily backup leave the database for a Discord
    channel. Lucas decided neither may be readable there: both are encrypted to one
    age public key (SSM `/keiko/backup/age_public_key`, `BACKUP_AGE_PUBLIC_KEY`
    locally), and only the matching private key, which the bot never holds, opens
    them (`age -d -i key.txt`).

    Guaranteed: a sealed payload round-trips with the private key and with no other;
    a missing, blank or malformed public key reads as no key, so a caller posts
    nothing; the largest payload `plaintext_limit` allows still fits the upload limit
    once sealed, whatever stanza the age header draws; production reads the key from
    SSM and boots without it, and a local run reads it from the environment.
    """
    sealed = logs_archive.seal(b"guild documents", logs_archive.age_recipient(PUBLIC_KEY))

    assert b"guild documents" not in sealed
    assert pyrage.decrypt(sealed, [IDENTITY]) == b"guild documents"
    with pytest.raises(pyrage.DecryptError):
        pyrage.decrypt(sealed, [x25519.Identity.generate()])


@pytest.mark.parametrize("public_key", ["", "   ", "not-an-age-key", None])
def test_a_missing_or_malformed_public_key_reads_as_no_key(public_key):
    assert logs_archive.age_recipient(public_key) is None


def test_a_public_key_with_spaces_around_it_is_read():
    assert str(logs_archive.age_recipient(f"  {PUBLIC_KEY}\n")) == PUBLIC_KEY


@pytest.mark.parametrize("limit", [5000, 8 * 1024, 64 * 1024, 64 * 1024 + 1, TEN_MB])
def test_the_largest_payload_still_fits_the_limit_once_sealed(limit):
    """The age header carries a random stanza, so the same size is sealed many times."""
    payload = os.urandom(logs_archive.plaintext_limit(limit))
    recipient = IDENTITY.to_public()
    tries = 3 if limit == TEN_MB else 50

    sealed = max(len(logs_archive.seal(payload, recipient)) for _ in range(tries))

    assert sealed <= limit


def test_production_reads_the_public_key_from_ssm(monkeypatch):
    config, ssm = production_config(monkeypatch)

    assert config.BACKUP_AGE_PUBLIC_KEY == f"value of {KEY_PARAMETER}"
    assert KEY_PARAMETER in ssm.asked


def test_a_public_key_never_created_leaves_nothing_to_seal_with(monkeypatch):
    config, _ = production_config(monkeypatch, missing={KEY_PARAMETER})

    assert config.BACKUP_AGE_PUBLIC_KEY == ""
    assert logs_archive.age_recipient(config.BACKUP_AGE_PUBLIC_KEY) is None


def test_a_local_run_reads_the_public_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("BACKUP_AGE_PUBLIC_KEY", PUBLIC_KEY)
    config = AppConfig.__new__(AppConfig)

    config.get_local_configs()

    assert config.BACKUP_AGE_PUBLIC_KEY == PUBLIC_KEY


JOINED = datetime(2025, 3, 1, 12, 30)


SECRET = "sk-not-for-discord"


RECIPIENT = IDENTITY.to_public()


class Channel:
    def __init__(self, limit=TEN_MB):
        self.guild = SimpleNamespace(filesize_limit=limit)
        self.sends = []

    async def send(self, content=None, file=None):
        self.sends.append((content, file))

    def history(self, limit=100, before=None, **kwargs):
        """A channel with nothing older on it: the retention suite covers what is."""

        async def nothing():
            return
            yield

        return nothing()


async def run_backup(channel, public_key=PUBLIC_KEY):
    """Drives the cog's task body without starting its loop."""
    bot = SimpleNamespace(
        config=SimpleNamespace(
            ADMIN_LOGS_FILES_CHANNEL_ID=7, BACKUP_AGE_PUBLIC_KEY=public_key
        ),
        get_channel=lambda _id: channel,
        user=SimpleNamespace(id=4242),
    )
    return await Analytics.post_daily_backup.coro(SimpleNamespace(bot=bot))


@pytest.fixture
def stored_databases(deps):
    """A small Keiko database: settings, the audit trail, birthdays and configs."""
    client = deps.mongo_client
    moderation_id = ObjectId()
    client.guild.moderations.insert_one(
        {"_id": moderation_id, "guild_id": "1", "block_links": True, "created_at": JOINED}
    )
    client.guild.moderations.insert_one(
        {"_id": ObjectId(), "guild_id": "2", "block_links": False}
    )
    client.guild.block_links.insert_one(
        {"_id": ObjectId(), "guild_id": "1", "enabled": True}
    )
    client.events.block_links.insert_one(
        {"_id": ObjectId(), "guild_id": "1", "event": "enabled"}
    )
    client.reminders.birthdays.insert_one(
        {"_id": ObjectId(), "guild_id": "1", "user_id": "555", "date": "05-12"}
    )
    client.configs.data.insert_one({"_id": ObjectId(), "status": "online"})
    client.configs.integrations.insert_one(
        {"_id": ObjectId(), "name": "openai", "configs": {"openai_api_key": SECRET}}
    )
    client.guild.logs.insert_one({"_id": ObjectId(), "message": "a debug line"})
    return SimpleNamespace(moderation_id=moderation_id)


def files_in(sent):
    """The zip a posted file holds, opened with the private key."""
    plain = pyrage.decrypt(sent.fp.getvalue(), [IDENTITY])
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def noise(size):
    """Text that does not compress, so a test controls how large a zip gets."""
    return base64.b64encode(os.urandom(size)).decode("ascii")


async def test_one_file_holds_every_collection(stored_databases):
    """The daily backup: Keiko's own collections, posted where the daily logs go.

    There was no backup at all: the database runs on a tier without one and the VPS
    has no cron for it, so a wrong click, a bad write or a manual mistake could not
    be undone. Once a day the `Backup` cog zips Keiko's collections as canonical
    Extended JSON Lines and posts the file on the logs files channel, beside the
    daily log export.

    Broke as (first version): the dump copied every database the Mongo user could
    list, so a secret in any other database reached Discord, and so did
    `audit.errors`, which keeps raw exception text that can quote a URL with an API
    key in it. Then (second version) a dump over the upload limit fell back one
    database at a time, so a large analytics collection kept every setting of the
    `guild` database out of the backup, with a warning nobody had to act on.

    Guaranteed: one file with every collection of Keiko's own databases (and of
    `configs`, only the collections named), whose documents come back with their
    `_id` and their types; nothing from any other database, and no third-party
    credentials, error audit or debug logs (already archived daily in the same
    channel); a dump over the channel's upload limit is packed one collection at a
    time into files under it, settings first and expiring analytics last, and a
    collection too large on its own is named in the pass's one summary message and
    in a warning; a failing pass is logged as an error and never stops the loop; a
    missing channel builds nothing and says so; and the logs tool, which ingests
    every `.log` and `.jsonl.gz` of that channel, never takes the backup for a log
    file.

    Then (third version, decided by Lucas): the database must not be readable in
    Discord. Every zip is encrypted to an age public key (SSM
    `/keiko/backup/age_public_key`, `BACKUP_AGE_PUBLIC_KEY` locally) before it is
    posted, so only the matching private key opens it (`age -d -i key.txt`); the bot
    never holds that key. Guaranteed as well: what is posted is never a readable
    zip, it round-trips with the private key and with no other, every encrypted file
    fits the channel's upload limit, and without a valid public key nothing is read
    or posted and a warning says why. Renewal rows, which expire only when nothing
    renews them, are packed with the settings, never with the collections that age
    out on their own.
    """
    channel = Channel()

    await run_backup(channel)

    assert len(channel.sends) == 1, "one message, one file"
    content, sent = channel.sends[0]
    assert sent.filename == f"keiko_backup_{today()}.zip.age"
    files = files_in(sent)
    assert sorted(files) == [
        "configs/data.jsonl",
        "events/block_links.jsonl",
        "guild/block_links.jsonl",
        "guild/moderations.jsonl",
        "manifest.json",
        "reminders/birthdays.jsonl",
    ]
    assert json.loads(files["manifest.json"])["collections"] == {
        "configs.data": 1,
        "events.block_links": 1,
        "guild.block_links": 1,
        "guild.moderations": 2,
        "reminders.birthdays": 1,
    }
    assert content == ml("messages.admin-logs.daily-backup", "en-us").format(
        date=today(), collections=5, documents=6
    )


async def test_documents_come_back_with_their_ids_and_types(stored_databases):
    channel = Channel()

    await run_backup(channel)

    lines = files_in(channel.sends[0][1])["guild/moderations.jsonl"].splitlines()
    restored = [json_util.loads(line) for line in lines]
    first = next(document for document in restored if document["guild_id"] == "1")
    assert first["_id"] == stored_databases.moderation_id
    assert first["created_at"] == JOINED
    assert first["block_links"] is True


async def test_credentials_and_debug_logs_stay_out(stored_databases):
    channel = Channel()

    await run_backup(channel)

    files = files_in(channel.sends[0][1])
    assert "configs/integrations.jsonl" not in files
    assert "guild/logs.jsonl" not in files
    assert not any(SECRET in text for text in files.values())


async def test_a_secret_outside_keikos_databases_or_in_error_audit_never_leaves(
    stored_databases, deps
):
    deps.mongo_client.scratch.tokens.insert_one({"_id": ObjectId(), "token": SECRET})
    deps.mongo_client.audit.errors.insert_one(
        {
            "_id": ObjectId(),
            "error_message": f"HTTPError for url: https://youtube.example/v3?key={SECRET}",
        }
    )
    deps.mongo_client.audit.reminders.insert_one({"_id": ObjectId(), "value": "x"})
    channel = Channel()

    await run_backup(channel)

    files = files_in(channel.sends[0][1])
    assert "audit/reminders.jsonl" in files, "Keiko's own audit is still copied"
    assert "audit/errors.jsonl" not in files
    assert not [name for name in files if name.startswith("scratch/")]
    assert not any(SECRET in text for text in files.values())


async def test_a_collection_added_to_configs_later_is_not_shipped(stored_databases, deps):
    deps.mongo_client.configs.admin.insert_one({"_id": ObjectId(), "admin_guild_id": "1"})
    deps.mongo_client.configs.webhooks.insert_one({"_id": ObjectId(), "secret": SECRET})
    channel = Channel()

    await run_backup(channel)

    files = files_in(channel.sends[0][1])
    assert {"configs/admin.jsonl", "configs/data.jsonl"} <= set(files)
    assert "configs/webhooks.jsonl" not in files, "a new configs collection was shipped"
    assert not any(SECRET in text for text in files.values())


def test_expiring_collections_are_packed_after_the_settings(stored_databases, deps):
    """A collection whose documents expire a fixed time after they are written goes
    last; renewal rows, which expire only when nothing renews them, stay with the
    settings, since a renewal without its row stops."""
    deps.mongo_client.guild.analytics_events.insert_one({"_id": ObjectId(), "event": "x"})
    deps.mongo_client.guild.blocked_links.insert_one({"_id": ObjectId(), "guild_id": "1"})
    deps.mongo_client.audit.reminders.insert_one({"_id": ObjectId(), "value": "x"})

    order = [f"{database}.{name}" for database, name in logs_archive.backed_up_collections()]

    assert set(order[-2:]) == {"guild.analytics_events", "guild.blocked_links"}
    assert order.index("audit.reminders") < order.index("guild.moderations"), (
        "renewal rows were packed with the collections that age out"
    )


async def test_a_backup_over_the_limit_is_packed_into_files_under_it(stored_databases, deps):
    """About 3 KB of settings and 3 KB of audit: together over 5 KB, apart under."""
    deps.mongo_client.guild.block_links.insert_one(
        {"_id": ObjectId(), "guild_id": "3", "answer": noise(2400)}
    )
    deps.mongo_client.events.block_links.insert_one(
        {"_id": ObjectId(), "guild_id": "3", "event": noise(2400)}
    )
    channel = Channel(limit=5000)

    await run_backup(channel)

    parts = [part for _content, part in channel.sends]
    assert len(parts) >= 2
    assert all(len(part.fp.getvalue()) <= 5000 for part in parts)
    assert [part.filename for part in parts] == [
        f"keiko_backup_{today()}_{index}-of-{len(parts)}.zip.age"
        for index in range(1, len(parts) + 1)
    ]
    assert channel.sends[0][0], "the first message says what the pass sent"
    assert [content for content, _part in channel.sends[1:]] == [None] * (len(parts) - 1)
    members = set()
    for part in parts:
        members |= {name for name in files_in(part) if name != "manifest.json"}
    assert members == {
        "configs/data.jsonl",
        "events/block_links.jsonl",
        "guild/block_links.jsonl",
        "guild/moderations.jsonl",
        "reminders/birthdays.jsonl",
    }


async def test_the_settings_go_out_when_only_they_fit_under_the_limit(
    stored_databases, deps, caplog
):
    for index in range(40):
        deps.mongo_client.guild.analytics_events.insert_one(
            {"_id": ObjectId(), "guild_id": str(index), "event": noise(1500)}
        )
    channel = Channel(limit=8 * 1024)

    with caplog.at_level(logging.WARNING):
        await run_backup(channel)

    sent = set()
    for _content, part in channel.sends:
        if part is not None:
            sent |= set(files_in(part))
    assert {
        "configs/data.jsonl",
        "events/block_links.jsonl",
        "guild/block_links.jsonl",
        "guild/moderations.jsonl",
        "reminders/birthdays.jsonl",
    } <= sent, "the settings were left out with the analytics"
    assert "guild/analytics_events.jsonl" not in sent
    assert "guild.analytics_events" in channel.sends[0][0], "the summary names it"
    assert "guild.analytics_events" in caplog.text


async def test_a_failing_pass_is_an_error_and_never_takes_down_the_loop(
    monkeypatch, caplog
):
    def explode(*_args):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(logs_archive, "build_backup", explode)

    with caplog.at_level(logging.WARNING):
        assert await run_backup(Channel()) is None

    failures = [
        record for record in caplog.records if "Daily backup failed" in record.getMessage()
    ]
    assert failures and failures[0].levelno == logging.ERROR
    assert "mongo is down" in caplog.text


async def test_a_missing_channel_builds_and_sends_nothing_and_says_so(
    monkeypatch, caplog
):
    built = []
    monkeypatch.setattr(logs_archive, "build_backup", lambda *args: built.append(args))

    with caplog.at_level(logging.WARNING):
        await run_backup(None)

    assert built == [], "the database was read for a channel that does not exist"
    assert "Daily backup not posted" in caplog.text, "nobody was told"


def test_the_logs_tool_never_takes_the_backup_for_a_log_file(stored_databases, monkeypatch):
    from tools.keiko.logs import fetch

    now = datetime(2026, 1, 1)
    whole, _skipped = logs_archive.build_backup(now, TEN_MB, RECIPIENT)
    split, _skipped = logs_archive.build_backup(now, len(whole[0].payload) - 1, RECIPIENT)
    log_name = "keiko_logs_2026-01-01.jsonl.gz"
    attachments = [
        {"filename": part.filename, "url": f"https://cdn.example.com/{index}"}
        for index, part in enumerate([*whole, *split])
    ]
    attachments.append({"filename": log_name, "url": "https://cdn.example.com/log"})
    message = {"id": "1", "attachments": attachments}
    monkeypatch.setattr(fetch, "iter_messages", lambda channel=None: iter([message]))

    picked = [attachment["filename"] for attachment in fetch.iter_log_attachments("1")]

    assert picked == [log_name]


async def test_what_is_posted_opens_with_the_private_key_and_no_other(stored_databases):
    channel = Channel()

    await run_backup(channel)

    posted = channel.sends[0][1].fp.getvalue()
    assert not zipfile.is_zipfile(io.BytesIO(posted)), "a readable zip reached Discord"
    assert SECRET not in posted.decode("latin-1")
    assert "guild/moderations.jsonl" in files_in(channel.sends[0][1])
    with pytest.raises(pyrage.DecryptError):
        pyrage.decrypt(posted, [x25519.Identity.generate()])


@pytest.mark.parametrize("public_key", ["", "   ", "not-an-age-key", None])
async def test_without_a_valid_public_key_nothing_is_read_or_posted(
    stored_databases, monkeypatch, caplog, public_key
):
    built = []
    monkeypatch.setattr(logs_archive, "build_backup", lambda *args: built.append(args))
    channel = Channel()

    with caplog.at_level(logging.WARNING):
        await run_backup(channel, public_key=public_key)

    assert channel.sends == [], "the backup went out without being encrypted"
    assert built == [], "the database was read for a backup that could not be encrypted"
    assert "Daily backup not posted" in caplog.text
    assert "never posted unencrypted" in caplog.text


class MonthChannel:
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


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(analytics_cog, "datetime", Frozen)


async def run_archive(channel, public_key=PUBLIC_KEY):
    """Drives the cog's task body without starting its loop."""
    bot = bot_with(channel, public_key=public_key)
    return await Analytics.archive_months.coro(SimpleNamespace(bot=bot))


def last_month():
    return logs_archive.last_month(Frozen.now(timezone.utc))


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


@pytest.mark.usefixtures("frozen_clock")
async def test_each_months_events_go_out_in_one_file_with_one_message(deps):
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
    month = last_month()
    before = store_event(deps, month - timedelta(minutes=1))
    inside = [
        store_event(deps, month + timedelta(days=1)),
        store_event(deps, month + timedelta(days=20), guild_id="2"),
    ]
    store_event(deps, (month + timedelta(days=32)).replace(day=1))
    channel = MonthChannel()

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


@pytest.mark.usefixtures("frozen_clock")
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
    channel = MonthChannel()

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


@pytest.mark.usefixtures("frozen_clock")
async def test_no_session_id_ever_stored_reaches_the_archive(deps):
    """The daily log exports carry the same session id next to guild and user ids."""
    month = last_month()
    for day, session in ((1, "sess-raw-one"), (2, "sess-raw-one"), (3, "sess-raw-two")):
        store_event(deps, month + timedelta(days=day), session_id=session)
    channel = MonthChannel()

    await run_archive(channel)

    text = gzip.decompress(pyrage.decrypt(channel.sends[0][1].fp.getvalue(), [IDENTITY]))
    assert b"sess-raw" not in text, "a stored session id reached the archive"
    first, second, third = [line["session_id"] for line in lines_of(channel.sends[0][1])]
    assert first == second != third, "sessions no longer group inside the file"


@pytest.mark.usefixtures("frozen_clock")
def test_each_pass_hashes_the_sessions_with_a_key_of_its_own(deps):
    month = last_month()
    store_event(deps, month + timedelta(days=1), session_id="sess-raw-one")
    recipient = IDENTITY.to_public()

    passes = [logs_archive.build_month_files(month, TEN_MB, recipient) for _ in range(2)]

    hashed = [
        lines_of(SimpleNamespace(fp=io.BytesIO(files[0].payload)))[0]["session_id"]
        for files in passes
    ]
    assert hashed[0] != hashed[1], "two passes hashed a session the same way: the key was kept"


@pytest.mark.usefixtures("frozen_clock")
async def test_every_unposted_month_still_inside_the_events_life_goes_out_oldest_first(
    deps,
):
    """A month whose every pass failed (the key created after the month boundary, say)."""
    for month in (7, 8, 9):
        store_event(deps, datetime(2026, month, 10, tzinfo=timezone.utc))
    channel = MonthChannel()

    await run_archive(channel)

    posted = [sent.filename for _content, sent in channel.sends]
    assert posted == [
        "keiko_events_2026-08.jsonl.gz.age",
        "keiko_events_2026-09.jsonl.gz.age",
    ], "July started before the events' 90 days and must be left; August was missed"
    assert posted_months(deps) == {"2026-08", "2026-09"}


@pytest.mark.usefixtures("frozen_clock")
async def test_a_month_already_posted_is_not_posted_again(deps):
    mark_posted(deps, "2026-08")
    for month in (8, 9):
        store_event(deps, datetime(2026, month, 10, tzinfo=timezone.utc))
    channel = MonthChannel()

    await run_archive(channel)

    assert [sent.filename for _content, sent in channel.sends] == [
        "keiko_events_2026-09.jsonl.gz.age"
    ]


@pytest.mark.usefixtures("frozen_clock")
async def test_a_month_that_fails_never_holds_the_months_after_it(
    deps, monkeypatch, caplog
):
    for month in (8, 9):
        store_event(deps, datetime(2026, month, 10, tzinfo=timezone.utc))
    build = logs_archive.build_month_files

    def august_fails(month, *args):
        if month.month == 8:
            raise ConnectionError("mongo is down")
        return build(month, *args)

    monkeypatch.setattr(logs_archive, "build_month_files", august_fails)
    channel = MonthChannel()

    with caplog.at_level(logging.WARNING):
        await run_archive(channel)

    assert [sent.filename for _content, sent in channel.sends] == [
        "keiko_events_2026-09.jsonl.gz.age"
    ]
    assert "Monthly events archive failed for 2026-08: ConnectionError" in caplog.text
    assert posted_months(deps) == {"2026-09"}, "August is tried again on the next pass"


@pytest.mark.usefixtures("frozen_clock")
async def test_the_events_come_back_with_their_types(deps):
    stored = store_event(deps, last_month() + timedelta(days=3, hours=4))
    channel = MonthChannel()

    await run_archive(channel)

    restored = lines_of(channel.sends[0][1])[0]
    assert restored["_id"] == stored["_id"]
    assert restored["ts"] == stored["ts"]
    assert restored["v"] == 1


@pytest.mark.usefixtures("frozen_clock")
async def test_a_month_is_posted_once_even_when_its_first_day_was_missed(deps):
    store_event(deps, last_month() + timedelta(days=2))
    channel = MonthChannel()

    await run_archive(channel)
    await run_archive(channel)

    assert len(channel.sends) == 1, "the next daily pass posted the month again"
    marker = deps.mongo_client.guild.analytics_archives.find_one({"_id": label()})
    assert marker["events"] == 1 and marker["files"] == 1


@pytest.mark.usefixtures("frozen_clock")
async def test_a_month_without_events_posts_nothing_and_is_not_read_again(
    deps, monkeypatch
):
    channel = MonthChannel()

    await run_archive(channel)
    reads = []
    monkeypatch.setattr(
        analytics_data, "iter_events_between", lambda *args: reads.append(args) or []
    )
    await run_archive(channel)

    assert channel.sends == []
    assert reads == []


@pytest.mark.usefixtures("frozen_clock")
async def test_a_month_over_the_upload_limit_goes_in_files_under_it(deps):
    month = last_month()
    stored = [
        store_event(deps, month + timedelta(days=day), props={"padding": noise(1500)})
        for day in range(0, 28, 3)
    ]
    channel = MonthChannel(limit=5000)

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


@pytest.mark.usefixtures("frozen_clock")
async def test_an_archived_month_opens_with_the_private_key_and_no_other(deps):
    stored = store_event(deps, last_month() + timedelta(days=2))
    channel = MonthChannel()

    await run_archive(channel)

    posted = channel.sends[0][1].fp.getvalue()
    assert not posted.startswith(b"\x1f\x8b"), "a readable gzip reached Discord"
    assert stored["event_id"].encode() not in posted
    assert lines_of(channel.sends[0][1])[0]["event_id"] == stored["event_id"]
    with pytest.raises(pyrage.DecryptError):
        pyrage.decrypt(posted, [x25519.Identity.generate()])


@pytest.mark.usefixtures("frozen_clock")
@pytest.mark.parametrize("public_key", ["", "not-an-age-key", None])
async def test_without_a_valid_public_key_no_month_is_read_or_posted(
    deps, monkeypatch, caplog, public_key
):
    store_event(deps, last_month() + timedelta(days=2))
    reads = []
    monkeypatch.setattr(
        analytics_data, "iter_events_between", lambda *args: reads.append(args) or []
    )
    channel = MonthChannel()

    with caplog.at_level(logging.WARNING):
        await run_archive(channel, public_key=public_key)

    assert channel.sends == [], "the events went out without being encrypted"
    assert reads == [], "the events were read for a file that could not be encrypted"
    assert "Monthly events archive not posted" in caplog.text
    assert "never posted unencrypted" in caplog.text
    assert deps.mongo_client.guild.analytics_archives.find_one({"_id": label()}) is None


@pytest.mark.usefixtures("frozen_clock")
async def test_a_missing_channel_posts_nothing_and_says_so(deps, caplog):
    store_event(deps, last_month() + timedelta(days=2))

    with caplog.at_level(logging.WARNING):
        await run_archive(None)

    assert "Monthly events archive not posted" in caplog.text
    assert deps.mongo_client.guild.analytics_archives.find_one({"_id": label()}) is None


@pytest.mark.usefixtures("frozen_clock")
async def test_a_failing_archive_pass_is_an_error_and_never_takes_down_the_loop(
    deps, monkeypatch, caplog
):
    def explode(*_args):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(logs_archive, "build_month_files", explode)

    with caplog.at_level(logging.WARNING):
        assert await run_archive(MonthChannel()) is None

    failures = [
        record for record in caplog.records
        if "Monthly events archive failed" in record.getMessage()
    ]
    assert failures and failures[0].levelno == logging.ERROR


@pytest.mark.usefixtures("frozen_clock")
def test_the_logs_tool_never_takes_the_archive_for_a_log_file(deps, monkeypatch):
    from tools.keiko.logs import fetch

    month = last_month()
    for day in range(0, 28, 3):
        store_event(deps, month + timedelta(days=day), props={"padding": noise(1500)})
    recipient = IDENTITY.to_public()
    whole = logs_archive.build_month_files(month, TEN_MB, recipient)
    split = logs_archive.build_month_files(month, 5000, recipient)
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


@pytest.mark.usefixtures("frozen_clock")
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

    await Analytics.measure_guild_sizes.coro(SimpleNamespace(bot=bot_with(guilds=guilds)))

    first = profiles.find_one({"_id": "1"})
    assert first["size_bucket"] == "<50", "the current size replaces the one before"
    assert first["joined_at"] == joined, "what the profile held is kept"
    assert isinstance(first["size_measured_at"], datetime)
    assert profiles.find_one({"_id": "2"})["size_bucket"] == "500-5k"
    assert "size_bucket" not in profiles.find_one({"_id": "3"}), "an unknown size is not written"


@pytest.mark.usefixtures("frozen_clock")
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

    await Analytics.measure_guild_sizes.coro(SimpleNamespace(bot=bot_with(guilds=guilds)))

    assert [profile["_id"] for profile in profiles.find({})] == ["1"]


@pytest.mark.usefixtures("frozen_clock")
async def test_a_failing_measure_is_a_warning_that_never_takes_down_the_loop(
    deps, monkeypatch, caplog
):
    def explode(*_args):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(analytics_data, "update_profiles", explode)
    guilds = [SimpleNamespace(id=1, member_count=12)]

    with caplog.at_level(logging.WARNING):
        await Analytics.measure_guild_sizes.coro(SimpleNamespace(bot=bot_with(guilds=guilds)))

    assert "Guild sizes not recorded: ConnectionError" in caplog.text


@pytest.mark.usefixtures("frozen_clock")
async def test_archiving_a_month_never_freezes_the_bot(deps, monkeypatch):
    mark_posted(deps, "2026-08")
    event = {"event": "feature.setup_opened", "ts": last_month(), "props": {}}
    monkeypatch.setattr(analytics_data, "iter_events_between", blocking([event]))
    channel = MonthChannel()

    ticks = await ticks_while(run_archive(channel))

    assert ticks >= MIN_TICKS
    assert len(channel.sends) == 1


@pytest.mark.usefixtures("frozen_clock")
async def test_measuring_the_guilds_never_freezes_the_bot(deps, monkeypatch):
    monkeypatch.setattr(analytics_data, "update_profiles", blocking())
    guilds = [SimpleNamespace(id=1, member_count=12)]

    ticks = await ticks_while(
        Analytics.measure_guild_sizes.coro(SimpleNamespace(bot=bot_with(guilds=guilds)))
    )

    assert ticks >= MIN_TICKS


BOT_ID = 4242


SOMEONE_ELSE = 777


PASS_AT = datetime(2026, 10, 6, 1, tzinfo=timezone.utc)


def refused():
    return discord.Forbidden(
        type("Response", (), {"status": 403, "reason": "Forbidden"})(),
        {"code": 50013, "message": "Missing Permissions"},
    )


def gone():
    return discord.NotFound(
        type("Response", (), {"status": 404, "reason": "Not Found"})(),
        {"code": 10008, "message": "Unknown Message"},
    )


class Posted:
    """A message already on the channel: who posted it, its files, how old it is (from
    `now`, the real clock by default), and how its deletes go."""

    def __init__(
        self, *filenames, days_ago, author_id=BOT_ID, failures=0, gone_already=False, now=None
    ):
        self.author = SimpleNamespace(id=author_id)
        self.attachments = [SimpleNamespace(filename=name) for name in filenames]
        self.created_at = (now or datetime.now(timezone.utc)) - timedelta(days=days_ago)
        self.failures = failures
        self.gone_already = gone_already
        self.attempts = 0
        self.deleted = False

    @property
    def name(self):
        return " + ".join(attachment.filename for attachment in self.attachments)

    async def delete(self):
        self.attempts += 1
        if self.gone_already:
            self.deleted = True
            raise gone()
        if self.failures:
            self.failures -= 1
            raise refused()
        self.deleted = True


class FilesChannel:
    """The logs files channel: what is on it, what the backup sends to it, and what
    each pass read of its history."""

    def __init__(self, *posted, history_fails=False, history_fails_after=None):
        self.guild = SimpleNamespace(filesize_limit=TEN_MB)
        self.posted = list(posted)
        self.history_fails = history_fails
        self.history_fails_after = history_fails_after
        self.sends = []
        self.walks = []

    async def send(self, content=None, file=None):
        self.sends.append((content, file))

    def history(self, limit=100, before=None, after=None, oldest_first=None):
        """What Discord answers: what is still there between `after` and `before`,
        oldest first when asked or when `after` is given, newest first otherwise."""
        if self.history_fails:
            raise refused()
        oldest = oldest_first if oldest_first is not None else after is not None
        walked = []
        self.walks.append(walked)

        async def walk():
            ordered = sorted(
                self.posted, key=lambda posted: posted.created_at, reverse=not oldest
            )
            for message in ordered:
                inside = (before is None or message.created_at < before) and (
                    after is None or message.created_at > after
                )
                if inside and not message.deleted:
                    if len(walked) == self.history_fails_after:
                        raise refused()
                    walked.append(message)
                    yield message

        return walk()


async def run_backup_pass(channel, public_key=PUBLIC_KEY):
    """Drives the cog's task body without starting its loop."""
    bot = SimpleNamespace(
        config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=7, BACKUP_AGE_PUBLIC_KEY=public_key),
        get_channel=lambda _id: channel,
        user=SimpleNamespace(id=BOT_ID),
    )
    return await Analytics.post_daily_backup.coro(SimpleNamespace(bot=bot))


def still_there(posted):
    return [message.name for message in posted if not message.deleted]


def deleted(posted):
    return [message.name for message in posted if message.deleted]


def warnings_with(caplog, text):
    return [record for record in caplog.records if text in record.getMessage()]


@pytest.fixture
def stored_settings(deps):
    deps.mongo_client.guild.moderations.insert_one({"_id": ObjectId(), "guild_id": "1"})
    deps.mongo_client.events.block_links.insert_one({"_id": ObjectId(), "event": "enabled"})


@pytest.fixture
def text_log_name(tmp_path, monkeypatch):
    """The name Discord gets for the text log the rotating handler posts."""
    monkeypatch.chdir(tmp_path)
    hooks = SimpleNamespace()
    LoggerHooks.set_timed_rotating_file_handler(hooks)
    posted = discord.File(hooks.file_handler.baseFilename)
    posted.close()
    hooks.file_handler.close()
    return posted.filename


async def test_each_kind_goes_past_its_age_and_stays_inside_it(stored_settings):
    """Keiko's own files on the logs files channel stay only as long as their kind allows.

    Broke as: every file Keiko posts on that channel stayed forever. The daily
    backup, a sealed copy of the whole database, piled up one copy a day (review
    round 2); the daily log exports, and the text log files before them, carry
    guild, user and session ids and piled up for years; and the walk that deleted
    the old backups paged the whole channel every day (review S6). Lucas set the
    rule by kind: backups 30 days, daily logs (the `.jsonl.gz` export and the
    `keiko_log.log` text file) 90 days, the monthly events archive for good, and
    nothing else is ever deleted.

    Shared behaviour: `logs_archive.delete_old_files`, run by `Analytics.post_daily_backup`
    on the channel the daily log export, the text log file and the monthly archive
    post to. Consumers that exposed it: the daily backup (roadmap #28) and the daily
    log export (Lucas, review round 2 of v1 wave 2).

    Broke as (closing review of round 4): nothing pinned the clear mark, so setting it
    to now, or to the shortest age, or moving it on a day without a backup, would
    have stopped the 90-day deletion with every test green; and the operator report
    counted a file someone else had deleted as a failure, repeated one error once per
    file, left the failed files out of what was left, and said "not deleted" for a
    pass that had deleted files before it stopped.

    Guaranteed: each kind is deleted past its age and kept inside it, recognized by
    the bot as its author and by the exact names its producer gives (the text log's
    from the rotating handler itself); the archive, the files of any other author,
    any other name and a message mixing kinds are kept at any age; the old backups go
    only on a day a backup went out, the old daily logs every day; a pass attempts at
    most `LOGS_FILES_DELETES_PER_PASS` deletes, oldest first, failed ones included,
    and says how many are left; once a pass leaves nothing behind, the next ones walk
    only the history inside the longest retention, never the whole channel again,
    yet a log that was young when the mark was set still goes once old, and a day
    without a backup never moves the mark past an old backup; a file already gone
    counts as deleted; a delete that fails is one warning per pass, each error once
    with its count, its file counted as left and tried again on the next pass; and a
    history read or a mark write that fails says the pass stopped, after how many,
    and never fails the backup.
    """
    past = [
        Posted("keiko_backup_2026-09-01.zip.age", days_ago=31),
        Posted("keiko_backup_2026-08-20_1-of-2.zip.age", days_ago=45),
        Posted("keiko_backup_2026-08-20_2-of-2.zip.age", days_ago=45),
        Posted("keiko_logs_2026-07-01.jsonl.gz", days_ago=91),
        Posted("keiko_log.log", days_ago=91),
        Posted("keiko_log.log", days_ago=500),
    ]
    inside = [
        Posted("keiko_backup_2026-09-10.zip.age", days_ago=29),
        Posted("keiko_logs_2026-07-10.jsonl.gz", days_ago=89),
        Posted("keiko_log.log", days_ago=89),
        Posted("keiko_events_2025-08.jsonl.gz.age", days_ago=400),
        Posted("keiko_events_2025-09_1-of-2.jsonl.gz.age", days_ago=380),
    ]
    channel = FilesChannel(*past, *inside)

    await run_backup_pass(channel)

    assert channel.sends, "today's backup was posted"
    assert still_there(past) == [], "a file past its kind's age stayed"
    assert deleted(inside) == [], "a file inside its kind's age, or the archive, was deleted"
    assert (Commands.BACKUP_RETENTION_DAYS, Commands.DAILY_LOGS_RETENTION_DAYS) == (30, 90)


@pytest.mark.shared_contract("logs_files")
async def test_every_name_keiko_gives_its_files_is_known_by_its_kind(
    stored_settings, deps, text_log_name
):
    for collection in (deps.mongo_client.guild.moderations, deps.mongo_client.events.block_links):
        collection.insert_one({"_id": ObjectId(), "noise": noise(3000)})
    month = datetime(2025, 9, 1, tzinfo=timezone.utc)
    for day in range(0, 28, 3):
        deps.mongo_client.guild.analytics_events.insert_one(
            {
                "_id": ObjectId(),
                "event": "feature.setup_opened",
                "ts": month + timedelta(days=day),
                "props": {"padding": noise(1500)},
            }
        )
    recipient = x25519.Identity.generate().to_public()
    whole, _skipped = logs_archive.build_backup(month, TEN_MB, recipient)
    split, _skipped = logs_archive.build_backup(month, len(whole[0].payload) - 1, recipient)
    archived = [
        *logs_archive.build_month_files(month, TEN_MB, recipient),
        *logs_archive.build_month_files(month, 5000, recipient),
    ]
    backups = [Posted(part.filename, days_ago=400) for part in [*whole, *split]]
    logs = [
        Posted(FILENAME.format(date="2025-09-01"), days_ago=400),
        Posted(text_log_name, days_ago=400),
    ]
    events = [Posted(part.filename, days_ago=400) for part in archived]
    channel = FilesChannel(*backups, *logs, *events)

    await run_backup_pass(channel)

    assert len(split) == 2 and len(archived) >= 3
    assert still_there(backups) == [], "a name the backup gives was not known"
    assert still_there(logs) == [], "a name the daily logs have was not known"
    assert deleted(events) == [], "the monthly events archive was deleted"


async def test_other_authors_and_other_names_stay_at_any_age(stored_settings):
    kept = [
        Posted("keiko_backup_2026-08-01.zip.age", days_ago=400, author_id=SOMEONE_ELSE),
        Posted("keiko_logs_2025-08-01.jsonl.gz", days_ago=400, author_id=SOMEONE_ELSE),
        Posted("keiko_log.log", days_ago=400, author_id=SOMEONE_ELSE),
        Posted("keiko_logs_2025-08-01.jsonl.gz", "notes.txt", days_ago=400),
        Posted("keiko_backup_2025-08-02.zip.age", "keiko_logs_2025-08-02.jsonl.gz", days_ago=400),
        Posted("keiko_logs_2025-08-03.jsonl.gzip", days_ago=400),
        Posted("keiko_logs_2025-08-04.jsonl.gz.bak", days_ago=400),
        Posted("old_keiko_log.log", days_ago=400),
        Posted("keiko_log.log.1", days_ago=400),
        Posted("keiko_2025-08-05.log", days_ago=400),
        Posted("keiko_backup_latest.zip.age", days_ago=400),
        Posted("keiko_backup_2025-08-06.zip", days_ago=400),
        Posted(days_ago=400),
    ]
    channel = FilesChannel(*kept)

    await run_backup_pass(channel)

    assert deleted(kept) == []


@pytest.mark.parametrize("failure", ["no key", "nothing fit", "post refused"])
async def test_old_backups_go_only_on_a_day_a_backup_went_out_old_logs_every_day(
    stored_settings, monkeypatch, failure
):
    old_backup = Posted("keiko_backup_2026-08-01.zip.age", days_ago=40)
    old_logs = [
        Posted("keiko_logs_2026-06-01.jsonl.gz", days_ago=120),
        Posted("keiko_log.log", days_ago=120),
    ]
    channel = FilesChannel(old_backup, *old_logs)
    public_key = None if failure == "no key" else PUBLIC_KEY
    if failure == "nothing fit":
        monkeypatch.setattr(logs_archive, "build_backup", lambda *args: ([], ["guild.moderations"]))
    if failure == "post refused":

        async def refuse(content=None, file=None):
            raise refused()

        channel.send = refuse

    await run_backup_pass(channel, public_key=public_key)

    assert not old_backup.deleted, "the last copies were deleted on a day nothing was backed up"
    assert still_there(old_logs) == [], "the old daily logs waited for a backup"


async def test_a_pass_deletes_at_most_its_cap_oldest_first_and_says_how_many_are_left(
    stored_settings, monkeypatch, caplog
):
    monkeypatch.setattr(Commands, "LOGS_FILES_DELETES_PER_PASS", 3, raising=False)
    old = [
        Posted(f"keiko_backup_2026-06-0{day}.zip.age", days_ago=110 - day)
        for day in range(1, 8)
    ]
    channel = FilesChannel(*old)

    with caplog.at_level(logging.INFO):
        await run_backup_pass(channel)

    assert [message.deleted for message in old] == [True] * 3 + [False] * 4, (
        "a pass deleted more than its cap, or not the oldest first"
    )
    assert "4 left" in caplog.text

    await run_backup_pass(channel)
    await run_backup_pass(channel)

    assert still_there(old) == [], "what a capped pass left was never deleted"


async def test_once_a_pass_leaves_nothing_the_next_walks_only_inside_the_longest_retention(
    stored_settings,
):
    kept_forever = [
        Posted(f"keiko_events_2025-{month:02d}.jsonl.gz.age", days_ago=400 - 30 * month)
        for month in range(1, 13)
    ]
    someone_else = [Posted("notes.txt", days_ago=600, author_id=SOMEONE_ELSE)]
    old_backup = Posted("keiko_backup_2026-08-01.zip.age", days_ago=40)
    channel = FilesChannel(*kept_forever, *someone_else, old_backup)

    await run_backup_pass(channel)
    await run_backup_pass(channel)

    assert old_backup.deleted
    edge = datetime.now(timezone.utc) - timedelta(days=91)
    walked_again = [message.name for message in channel.walks[-1] if message.created_at < edge]
    assert walked_again == [], "the next pass paged the whole channel again"


async def test_a_log_young_when_the_clear_mark_was_set_still_goes_once_old(deps):
    young = Posted("keiko_log.log", days_ago=40, now=PASS_AT)
    channel = FilesChannel(young)

    await logs_archive.delete_old_files(channel, BOT_ID, PASS_AT, with_backups=True)
    await logs_archive.delete_old_files(
        channel, BOT_ID, PASS_AT + timedelta(days=60), with_backups=True
    )

    assert young.deleted, "the clear mark hid a log that was not old yet when it was set"


async def test_a_day_without_a_backup_never_moves_the_mark_past_old_backups(deps):
    old_backup = Posted("keiko_backup_2026-06-01.zip.age", days_ago=100, now=PASS_AT)
    channel = FilesChannel(old_backup)

    await logs_archive.delete_old_files(channel, BOT_ID, PASS_AT, with_backups=False)
    await logs_archive.delete_old_files(
        channel, BOT_ID, PASS_AT + timedelta(days=1), with_backups=True
    )

    assert old_backup.deleted, "a pass without backups moved the mark past an old backup"


async def test_when_every_delete_fails_the_attempts_stay_within_the_cap(
    stored_settings, monkeypatch
):
    monkeypatch.setattr(Commands, "LOGS_FILES_DELETES_PER_PASS", 3)
    refusing = [
        Posted(f"keiko_logs_2026-05-0{day}.jsonl.gz", days_ago=130 - day, failures=99)
        for day in range(1, 8)
    ]
    channel = FilesChannel(*refusing)

    await run_backup_pass(channel)

    assert sum(message.attempts for message in refusing) == 3, (
        "failed deletes did not count against the cap"
    )


async def test_failures_are_grouped_by_error_and_counted_as_left(
    stored_settings, monkeypatch, caplog
):
    monkeypatch.setattr(Commands, "LOGS_FILES_DELETES_PER_PASS", 3)
    refusing = [
        Posted(f"keiko_logs_2026-05-0{day}.jsonl.gz", days_ago=130 - day, failures=99)
        for day in range(1, 8)
    ]
    channel = FilesChannel(*refusing)

    with caplog.at_level(logging.WARNING):
        await run_backup_pass(channel)

    [warning] = warnings_with(caplog, "could not be deleted")
    report = warning.getMessage()
    assert report.count("Missing Permissions") == 1, "one error was repeated for each file"
    assert "Missing Permissions (3)" in report
    assert "7 left" in report, "the files whose delete failed were not counted as left"


async def test_a_file_already_gone_counts_as_deleted(stored_settings, caplog):
    gone_already = Posted("keiko_logs_2026-06-01.jsonl.gz", days_ago=120, gone_already=True)
    kept_forever = Posted("keiko_events_2025-01.jsonl.gz.age", days_ago=400)
    channel = FilesChannel(gone_already, kept_forever)

    with caplog.at_level(logging.INFO):
        await run_backup_pass(channel)
        await run_backup_pass(channel)

    assert "could not be deleted" not in caplog.text, "a file already gone was a failure"
    assert "left for the next passes" not in caplog.text
    assert kept_forever not in channel.walks[-1], "a file already gone kept the mark back"


async def test_a_history_that_breaks_after_some_deletes_says_the_pass_stopped(
    stored_settings, caplog
):
    old = [
        Posted(f"keiko_logs_2026-05-0{day}.jsonl.gz", days_ago=130 - day)
        for day in range(1, 5)
    ]
    channel = FilesChannel(*old, history_fails_after=2)

    with caplog.at_level(logging.WARNING):
        await run_backup_pass(channel)

    assert channel.sends, "the backup went out"
    [warning] = warnings_with(caplog, "logs files channel")
    assert warning.levelno == logging.WARNING
    assert "stopped after 2 deleted" in warning.getMessage()
    assert "Daily backup failed" not in caplog.text


async def test_a_mark_that_cannot_be_written_says_the_pass_stopped(
    stored_settings, monkeypatch, caplog
):
    def refuse(*_args):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr("app.data.logs.update_retention", refuse)
    old = Posted("keiko_logs_2026-06-01.jsonl.gz", days_ago=120)
    channel = FilesChannel(old)

    with caplog.at_level(logging.WARNING):
        await run_backup_pass(channel)

    assert old.deleted
    [warning] = warnings_with(caplog, "logs files channel")
    assert "stopped after 1 deleted" in warning.getMessage()
    assert "ConnectionError: mongo is down" in warning.getMessage()


async def test_a_delete_that_fails_only_warns_and_is_tried_again(stored_settings, caplog):
    stuck = Posted("keiko_logs_2026-06-01.jsonl.gz", days_ago=121, failures=1)
    old = Posted("keiko_backup_2026-08-02.zip.age", days_ago=40)
    channel = FilesChannel(stuck, old)

    with caplog.at_level(logging.WARNING):
        await run_backup_pass(channel)

    assert channel.sends, "the backup went out"
    assert old.deleted and not stuck.deleted, "one failed delete stopped the others"
    warnings = [
        record for record in caplog.records if "could not be deleted" in record.getMessage()
    ]
    assert warnings, "a failed delete was not reported"
    assert all(record.levelno == logging.WARNING for record in warnings)
    assert "Daily backup failed" not in caplog.text

    await run_backup_pass(channel)

    assert stuck.deleted, "the next pass skipped what a failed delete left"


async def test_a_history_that_cannot_be_read_only_warns(stored_settings, caplog):
    channel = FilesChannel(history_fails=True)

    with caplog.at_level(logging.WARNING):
        await run_backup_pass(channel)

    assert channel.sends, "the backup went out"
    [warning] = warnings_with(caplog, "logs files channel")
    assert warning.levelno == logging.WARNING
    assert "stopped after 0 deleted" in warning.getMessage()
    assert "Daily backup failed" not in caplog.text


def test_the_ttl_index_is_created_in_the_audit_database():
    """The indexes Keiko creates at startup reach the database each one names.

    Broke as: `audit.reminders` only grew, so its renewal rows got a TTL index, the first
    index outside the `guild` database. Guaranteed: an index may name a collection of another
    database as `database.collection`; a database name holds no dot, a collection name may.
    """
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

"""Debug logs: the hot window of "why did it break?", kept in Mongo for 30 days.

This is the fourth kind of signal in docs/analytics.md, and the only one that
had no durable home: operational lives in Prometheus, product analytics in
`guild.analytics_*`, audit in `events.<cog_key>`, and debug lived only in a log
file that the container deletes and in Discord embeds that truncate tracebacks.

Deliberately NOT `analytics.emit`: that path validates against the catalog and
`sanitize_props` strips free text, which is exactly what a log message and a
traceback are. Same rails — a bounded queue drained by the Analytics cog — and
a separate destination, so neither contract has to bend for the other.

Writes leave on a dedicated thread, not on a timer. `logging` runs on the
thread that called it, which for a command is the event loop, and `pymongo` is
synchronous — so writing inside `emit` would put a network round trip in front
of every one of the ~108 log calls in `app/`. The writer instead blocks on
`queue.get()`, so a line on a quiet bot is written in microseconds, and drains
whatever else is already queued into one `insert_many`, so a burst on
`on_message` costs one round trip per 200 lines rather than 200.

Two invariants the tests pin:

- Recording a log never blocks, never raises, and never grows without bound.
- Persisting a log never logs. The sink runs underneath `logging`, so a warning
  about a failed write would be recorded, fail, and warn again. Write failures
  go to stderr via `report_flush_failure`, and a record that cannot even be
  built goes out through `logging.Handler.handleError`.
"""
import queue
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.constants import Commands as constants
from app.services.trace import current_trace

SCHEMA_VERSION = 1

_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue(
    maxsize=constants.DEBUG_LOGS_QUEUE_MAXSIZE
)
_ENABLED = True
_ENVIRONMENT = "dev"
_STATS: Dict[str, int] = {"recorded": 0, "dropped": 0, "flushed": 0, "failed": 0}
_SENTINEL = object()
_writer: Optional["_Writer"] = None


def configure(config: Any) -> None:
    global _ENABLED, _ENVIRONMENT
    _ENABLED = bool(getattr(config, "DEBUG_LOGS_ENABLED", True))
    _ENVIRONMENT = "prod" if getattr(config, "is_prod", bool)() else "dev"


def is_enabled() -> bool:
    return _ENABLED


def stats() -> Dict[str, int]:
    return {**_STATS, "queue_depth": _QUEUE.qsize()}


def record(document: Dict[str, Any]) -> bool:
    """Queue one already-built log document. Returns False when it was dropped."""
    if not _ENABLED:
        return False

    try:
        _QUEUE.put_nowait(document)
    except queue.Full:
        _STATS["dropped"] += 1
        return False

    _STATS["recorded"] += 1
    return True


def build_document(
    *,
    level: str,
    message: str,
    log_type: Optional[str] = None,
    module: Optional[str] = None,
    function: Optional[str] = None,
    line: Optional[int] = None,
    traceback_text: Optional[str] = None,
    guild_id: Optional[Any] = None,
    user_id: Optional[Any] = None,
    interaction_id: Optional[Any] = None,
    channel_id: Optional[Any] = None,
    ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    """One log line as a document, correlated to the trace that produced it.

    `session_id` is what makes this worth querying: filtering by it returns the
    whole timeline of a single interaction instead of one line out of context.
    """
    trace = current_trace()

    return {
        "v": SCHEMA_VERSION,
        "ts": ts or datetime.now(timezone.utc),
        "level": level,
        "message": _clip(message, constants.DEBUG_LOGS_MESSAGE_MAX_LENGTH),
        "log_type": log_type,
        "guild_id": _as_id(guild_id) or (trace.guild_id if trace else None),
        "user_id": _as_id(user_id) or (trace.user_id if trace else None),
        "interaction_id": _as_id(interaction_id),
        "channel_id": _as_id(channel_id),
        "feature": trace.feature if trace else None,
        "source": trace.source if trace else None,
        "session_id": trace.session_id if trace else None,
        "trace_id": trace.id if trace else None,
        "module": module,
        "function": function,
        "line": line,
        "traceback": _clip(traceback_text, constants.DEBUG_LOGS_TRACEBACK_MAX_LENGTH),
        "env": _ENVIRONMENT,
    }


def _as_id(value: Any) -> Optional[str]:
    return str(value) if value not in (None, "") else None


ELLIPSIS = "…\n"


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    """Keep the tail of an oversized value: the last frames explain the failure.

    The marker counts against the limit, so the result never exceeds it.
    """
    if not value:
        return None
    if len(value) <= limit:
        return value
    return ELLIPSIS + value[-(limit - len(ELLIPSIS)):]


def drain(limit: int = constants.DEBUG_LOGS_FLUSH_BATCH) -> List[Dict[str, Any]]:
    batch: List[Dict[str, Any]] = []
    while len(batch) < limit:
        try:
            batch.append(_QUEUE.get_nowait())
        except queue.Empty:
            break
    return batch


def report_flush_failure(error: Exception) -> None:
    """Where a failed write is reported, and why it is not the logger.

    This module drains the logger. A `logger.warn` here would be recorded by
    StoredLogsHandler, queued, fail on the same broken connection, and warn
    again. stderr is outside the logging tree, so it cannot feed itself.

    It is the default of `flush` rather than something a caller passes, because
    an invariant that depends on every caller remembering it is not an
    invariant.
    """
    print(
        f"[keiko] debug log flush failed: {type(error).__name__}: {error}",
        file=sys.stderr,
    )


def flush(on_error=report_flush_failure) -> int:
    """Drain synchronously. The writer thread covers production; this is the
    test path and the cog's backstop for a writer that never started."""
    flushed = 0
    for _ in range(constants.DEBUG_LOGS_FLUSH_MAX_BATCHES):
        batch = drain()
        if not batch:
            break
        written = _write(batch, on_error=on_error)
        if not written:
            break
        flushed += written

    return flushed


class _Writer(threading.Thread):
    """Drains the queue into Mongo as records arrive.

    Blocking `get` for the first record and `get_nowait` for the rest is what
    gives both properties at once: no latency when the bot is quiet, and
    automatic batching exactly when there is enough traffic to need it.
    """

    def __init__(self) -> None:
        super().__init__(name="keiko-debug-logs", daemon=True)

    def run(self) -> None:
        while True:
            first = _QUEUE.get()
            if first is _SENTINEL:
                return

            batch = [first]
            while len(batch) < constants.DEBUG_LOGS_FLUSH_BATCH:
                try:
                    batch.append(_QUEUE.get_nowait())
                except queue.Empty:
                    break

            if batch and batch[-1] is _SENTINEL:
                batch.pop()
                _write(batch)
                return

            _write(batch)


def _write(batch: List[Dict[str, Any]], on_error=None) -> int:
    """Imported lazily: `app.data.logs` reads `app.mongo_client`, which does not
    exist yet while `app.logger` is being imported."""
    if not batch:
        return 0

    from app.data import logs as logs_data

    try:
        logs_data.insert_logs(batch)
    except Exception as error:  # noqa: BLE001 - reported out of band, never logged
        _STATS["failed"] += len(batch)
        (on_error or report_flush_failure)(error)
        return 0

    _STATS["flushed"] += len(batch)
    return len(batch)


def start_writer() -> None:
    """Called once the Mongo client exists. Never started by the test suite,
    which drives `flush` directly so assertions stay deterministic."""
    global _writer
    if _writer is None or not _writer.is_alive():
        _writer = _Writer()
        _writer.start()


def stop_writer(timeout: float = 2.0) -> None:
    global _writer
    if _writer is None:
        return

    _QUEUE.put(_SENTINEL)
    _writer.join(timeout=timeout)
    _writer = None


def writer_running() -> bool:
    return _writer is not None and _writer.is_alive()


def reset() -> None:
    """Test hook: forget queued documents and counters."""
    global _ENABLED
    stop_writer()
    drain(limit=constants.DEBUG_LOGS_QUEUE_MAXSIZE)
    _ENABLED = True
    for key in _STATS:
        _STATS[key] = 0

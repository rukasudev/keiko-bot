"""Debug logs: the hot window of "why did it break?", kept in Mongo for 30 days.

This is the fourth kind of signal in docs/analytics.md, and the only one that
had no durable home: operational lives in Prometheus, product analytics in
`events.activity`, `events.interactions` and `guild.analytics_*`, changes in
`events.changes`, and debug lived only in a log
file that the container deletes and in Discord embeds that truncate tracebacks.

Deliberately NOT `analytics.record`: that path validates against the catalog and
`sanitize_props` strips free text, which is exactly what a log message and a
traceback are. Same rails — a bounded queue drained by the Analytics cog — and
a separate destination, so neither contract has to bend for the other.

Writes leave on a writer task of the bot's loop, not on a timer and not in
`emit`. `logging` runs on whatever thread called it, and the Mongo client lives
on the loop, so writing inside `emit` would put a round trip in front of every
log call in `app/`. Recording a line wakes the writer, so a line on a quiet bot
is written at once, and the writer drains whatever else is already queued into
one `insert_many`, so a burst on `on_message` costs one round trip per 200 lines
rather than 200.

Two invariants the tests pin:

- Recording a log never blocks, never raises, and never grows without bound.
- Persisting a log never logs. The sink runs underneath `logging`, so a warning
  about a failed write would be recorded, fail, and warn again. Write failures
  go to stderr via `report_flush_failure`, and a record that cannot even be
  built goes out through `logging.Handler.handleError`.
"""
import asyncio
import queue
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import app_version
from app.constants import Commands as constants
from app.core.trace import current_trace

SCHEMA_VERSION = 1

_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue(
    maxsize=constants.DEBUG_LOGS_QUEUE_MAXSIZE
)
_ENABLED = True
_ENVIRONMENT = "dev"
_STATS: Dict[str, int] = {"recorded": 0, "dropped": 0, "flushed": 0, "failed": 0}
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
    _wake_the_writer()
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
        "message": clip_tail(message, constants.DEBUG_LOGS_MESSAGE_MAX_LENGTH),
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
        "traceback": clip_tail(traceback_text, constants.DEBUG_LOGS_TRACEBACK_MAX_LENGTH),
        "env": _ENVIRONMENT,
        "app_version": app_version(),
    }


def _as_id(value: Any) -> Optional[str]:
    return str(value) if value not in (None, "") else None


ELLIPSIS = "…\n"


def clip_tail(value: Optional[str], limit: int) -> Optional[str]:
    """Keep the end: the last frames of a traceback are what explain it.

    The counterpart is `logger.clip_head`, which keeps the beginning because an
    embed is identified by its first line. The marker counts against the limit,
    so the result never exceeds it.
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


async def flush(on_error=report_flush_failure) -> int:
    """Drain the queue into Mongo, a bounded number of batches at a time."""
    flushed = 0
    for _ in range(constants.DEBUG_LOGS_FLUSH_MAX_BATCHES):
        batch = drain()
        if not batch:
            break
        written = await _write(batch, on_error=on_error)
        if not written:
            break
        flushed += written

    return flushed


async def _write(batch: List[Dict[str, Any]], on_error=None) -> int:
    """Imported lazily: `app.logger` imports this module before the data layer."""
    if not batch:
        return 0

    from app.data import data

    try:
        await data().guild_logs.insert_logs(batch)
    except Exception as error:  # noqa: BLE001 - reported out of band, never logged
        _STATS["failed"] += len(batch)
        (on_error or report_flush_failure)(error)
        return 0

    _STATS["flushed"] += len(batch)
    return len(batch)


class _Writer:
    """Drains the queue into Mongo as records arrive, on the loop that holds the client.

    Woken by every record, from any thread, it writes the first line at once and
    whatever is queued with it as one batch: no latency when the bot is quiet,
    and batching exactly when there is enough traffic to need it.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.woken = asyncio.Event()
        self.stopping = False
        self.task = loop.create_task(self._run())

    def wake(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.woken.set)
        except RuntimeError:
            pass

    async def _run(self) -> None:
        while True:
            await self.woken.wait()
            self.woken.clear()
            batch = drain()
            while batch:
                await _write(batch)
                batch = drain()
            if self.stopping:
                return


def _wake_the_writer() -> None:
    writer = _writer
    if writer is not None:
        writer.wake()


def start_writer() -> None:
    """Start the writer on the running loop, once the data layer is installed. Never
    started by the test suite, which drives `flush` directly so assertions stay
    deterministic."""
    global _writer
    if _writer is None or _writer.task.done():
        _writer = _Writer(asyncio.get_running_loop())
        _writer.wake()


async def stop_writer(timeout: float = 2.0) -> None:
    """Let the writer write what is queued, for up to `timeout` seconds, and stop it."""
    global _writer
    writer, _writer = _writer, None
    if writer is None:
        return

    writer.stopping = True
    writer.woken.set()
    done, _pending = await asyncio.wait({writer.task}, timeout=timeout)
    if not done:
        writer.task.cancel()


def writer_running() -> bool:
    return _writer is not None and not _writer.task.done()


def reset() -> None:
    """Test hook: forget queued documents, the writer and counters."""
    global _ENABLED, _writer
    _writer = None
    drain(limit=constants.DEBUG_LOGS_QUEUE_MAXSIZE)
    _ENABLED = True
    for key in _STATS:
        _STATS[key] = 0

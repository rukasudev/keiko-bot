"""One unit of work — an interaction, a webhook request, a scheduled job.

A context variable carries the active trace down through every call below it,
including across `await`, so a log line written deep in an integration knows
which trace it belongs to without receiving a new parameter. Sinks registered
here are called once, when the trace closes. Reference: docs/analytics.md
"""
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.constants import LogTypes as constants

_logger = logging.getLogger(__name__)
_current_trace: ContextVar[Optional["Trace"]] = ContextVar("keiko_trace", default=None)
_sinks: List[Callable[["Trace"], None]] = []


def register_sink(sink: Callable[["Trace"], None]) -> None:
    if sink not in _sinks:
        _sinks.append(sink)


def clear_sinks() -> None:
    _sinks.clear()


def current_trace() -> Optional["Trace"]:
    return _current_trace.get()


def new_trace_id() -> str:
    return uuid.uuid4().hex[:6]


def as_utc(moment: Optional[datetime]) -> Optional[datetime]:
    """Mongo hands timestamps back without a timezone; the clock here is aware.

    Every timestamp entering a trace passes through this, so a stored event and
    a live one can be subtracted from each other.
    """
    if not isinstance(moment, datetime):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class Trace:
    """The timeline of one unit of work, rendered as a single log message."""

    def __init__(
        self,
        name: str,
        *,
        source: Optional[str] = None,
        guild_id: Optional[Any] = None,
        user_id: Optional[Any] = None,
        feature: Optional[str] = None,
        session_id: Optional[str] = None,
        silent_when_clean: bool = False,
    ) -> None:
        self.id = new_trace_id()
        self.name = name
        self.source = source
        self.guild_id = str(guild_id) if guild_id else None
        self.user_id = str(user_id) if user_id else None
        self.feature = feature
        self.session_id = session_id or self.id
        self.silent_when_clean = silent_when_clean
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: Optional[datetime] = None
        self.result: Optional[str] = None
        self.lines: List[Dict[str, Any]] = []
        self.truncated = 0
        self.max_level = logging.NOTSET
        self.footnote: Optional[str] = None
        # What was last *done*, kept apart from `result`, which is the
        # lifecycle. A manager adds several items before it saves; the title
        # wants the action, the session wants to stay open.
        self.last_action: Optional[str] = None
        self.is_journey = False
        self.superseded = False
        self.message_id: Optional[int] = None

    def add(
        self,
        message: str,
        levelno: int = logging.INFO,
        timestamp: Optional[datetime] = None,
    ) -> None:
        self.max_level = max(self.max_level, levelno)

        if len(self.lines) >= constants.TRACE_MAX_LINES:
            self.truncated += 1
            return

        text = str(message).replace("\n", " ").strip()
        if len(text) > constants.TRACE_LINE_MAX_LENGTH:
            text = text[: constants.TRACE_LINE_MAX_LENGTH - 1] + "…"

        self.lines.append({
            "ts": as_utc(timestamp) or datetime.now(timezone.utc),
            "message": text,
            "levelno": levelno,
        })

    def finish(self, result: Optional[str] = None) -> None:
        if self.finished_at:
            return
        self.finished_at = datetime.now(timezone.utc)
        self.result = result or self._implicit_result()

    @property
    def duration_ms(self) -> int:
        end = as_utc(self.finished_at) or datetime.now(timezone.utc)
        start = as_utc(self.started_at) or end
        return int((end - start).total_seconds() * 1000)

    @property
    def has_error(self) -> bool:
        return self.max_level >= logging.ERROR

    @property
    def is_noteworthy(self) -> bool:
        """A clean routine trace can stay out of Discord and live in the file."""
        if self.superseded:
            return False
        if not self.silent_when_clean:
            return True
        return self.has_error or self.result == constants.TRACE_RESULT_FAILURE

    def supersede(self, journey: "Trace") -> None:
        """Hand this interaction's lines to the session that outlives it.

        Without this the same invocation would post twice: once when the
        interaction ends and once as the journey.
        """
        for line in self.lines:
            journey.lines.append(dict(line))
        self.superseded = True

    def _implicit_result(self) -> str:
        if self.has_error:
            return constants.TRACE_RESULT_FAILURE
        return constants.TRACE_RESULT_SUCCESS


class trace_scope:
    """Open a trace for the duration of a block, sync or async.

    Nested scopes reuse the outer trace so a webhook that fans out does not
    fragment its own timeline.
    """

    def __init__(self, name: str, opening: Optional[str] = None, **kwargs: Any) -> None:
        self.name = name
        self.opening = opening
        self.kwargs = kwargs
        self.trace: Optional[Trace] = None
        self._token = None
        self._owns_trace = False

    def _enter(self) -> Trace:
        existing = _current_trace.get()
        if existing:
            self.trace = existing
            return existing

        self.trace = Trace(self.name, **self.kwargs)
        self._token = _current_trace.set(self.trace)
        self._owns_trace = True

        if self.opening:
            # Logged, never `trace.add`. The Discord handler folds this into the
            # timeline and suppresses the separate embed, so one call reaches the
            # channel, the file and Mongo at once. Emitting it here, and only
            # when the trace is created, is also what keeps a command reached
            # through a button from announcing itself twice.
            _logger.info(self.opening)

        return self.trace

    def _exit(self, exc: Optional[BaseException]) -> None:
        if not self._owns_trace or not self.trace:
            return

        if exc is not None:
            self.trace.add(f"{type(exc).__name__}: {exc}", logging.ERROR)
        self.trace.finish()
        _current_trace.reset(self._token)
        emit_to_sinks(self.trace)

    def __enter__(self) -> Trace:
        return self._enter()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._exit(exc)
        return False

    async def __aenter__(self) -> Trace:
        return self._enter()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._exit(exc)
        return False


def emit_to_sinks(trace: Trace) -> None:
    """A failing sink never propagates into the work being traced."""
    for sink in list(_sinks):
        try:
            sink(trace)
        except Exception:
            pass


def add_line(message: str, levelno: int = logging.INFO) -> bool:
    """Record a line on the active trace, if there is one."""
    trace = _current_trace.get()
    if not trace:
        return False
    trace.add(message, levelno)
    return True


def has_open_trace() -> bool:
    return _current_trace.get() is not None

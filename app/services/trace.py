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
        quiet: bool = False,
    ) -> None:
        self.id = new_trace_id()
        self.name = name
        self.source = source
        self.guild_id = str(guild_id) if guild_id else None
        self.user_id = str(user_id) if user_id else None
        self.feature = feature
        self.session_id = session_id or self.id
        self.silent_when_clean = silent_when_clean
        self.quiet = quiet
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
        # The lifecycle event this trace reported, if any. It decides both
        # whether a silent trace is published and what the message is called.
        self.reported_event: Optional[str] = None

    def add(
        self,
        message: str,
        levelno: int = logging.INFO,
        timestamp: Optional[datetime] = None,
        log_type: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> None:
        self.max_level = max(self.max_level, levelno)

        if log_type in constants.REPORTED_EVENT_TYPES:
            # Kept even when the line itself is truncated away: what decides
            # whether this trace is published is that the event happened, not
            # whether its line fit.
            self.reported_event = log_type

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
            "log_type": log_type,
            "kind": kind,
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
        """A clean routine trace can stay out of Discord and live in the file.

        "Clean" is not the same as "empty of meaning". A listener that reported
        an event — a guild added Keiko, a guild removed it — did the one thing
        the log channel exists for, and it did it without failing. Silence there
        is how the "Left Guild" message disappeared while the record was sitting
        in `guild.logs` all along.
        """
        if self.superseded or self.quiet:
            return False
        if not self.silent_when_clean:
            return True
        return bool(self.reported_event) or self.has_error or (
            self.result == constants.TRACE_RESULT_FAILURE
        )

    def supersede(self, journey: "Trace") -> None:
        """Hand this interaction's lines to the session that outlives it.

        Without this the same invocation would post twice: once when the
        interaction ends and once as the journey.
        """
        for line in self.lines:
            journey.lines.append(dict(line))
        self.superseded = True

    def handover(self) -> "Trace":
        """The trace that carries this unit of work on after its first owner returns."""
        successor = Trace(
            self.name,
            source=self.source,
            guild_id=self.guild_id,
            user_id=self.user_id,
            feature=self.feature,
            session_id=self.session_id,
            silent_when_clean=self.silent_when_clean,
        )
        successor.started_at = self.started_at
        successor.footnote = self.footnote
        self.supersede(successor)
        return successor

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


async def run_traced(
    coroutine: Any, name: str, *, trace: Optional[Trace] = None, **kwargs: Any
) -> None:
    """Await work that outlives whatever scheduled it, under a trace of its own.

    A task inherits the context it was created in, so background work started
    from a request keeps pointing at that request's trace — which its scope
    closed, and posted, before the work ever ran. Every line the work logs then
    lands on a message nobody will look at again: that is why a Twitch
    `stream.online` fan-out produced a webhook message with an empty timeline.

    `trace_scope` is deliberately not reused here: it joins the surrounding
    trace so a fan-out does not fragment its timeline, which is the right rule
    inside one unit of work and the wrong one across two.

    A failure is recorded and swallowed. This is fire-and-forget work — there is
    no caller left to raise to, and an exception escaping into a task nobody
    awaits is only a warning on stderr.

    A `trace` handed over by the caller continues the caller's message.
    """
    trace = trace or Trace(name, **kwargs)
    token = _current_trace.set(trace)
    try:
        await coroutine
    except Exception:
        # Through `logging`, never `trace.add`: the traceback belongs in the
        # error channel and in the debug sink, and the timeline gets its line
        # from that same call.
        _logger.exception(f"{name} failed")
    finally:
        trace.finish()
        _current_trace.reset(token)
        emit_to_sinks(trace)


def emit_to_sinks(trace: Trace) -> None:
    """A failing sink never propagates into the work being traced."""
    for sink in list(_sinks):
        try:
            sink(trace)
        except Exception:
            pass


def add_line(
    message: str,
    levelno: int = logging.INFO,
    log_type: Optional[str] = None,
    guild_id: Optional[Any] = None,
) -> bool:
    """Record a line on the active trace, if there is one.

    A trace also learns its subject here when it could not know it: a listener
    receives a `discord.Guild`, which carries no `.guild` for the decorator to
    read, so `on_guild_remove` had a message that never said which guild left.
    The record knows — it was given `guild_id`.

    Only a reported event may settle it, which is narrower than it looks. Any
    line would work for a listener, and be wrong for a fan-out: one birthday job
    walks several guilds in a single trace, and the first line to mention one
    would label the whole message with it. An event is about exactly one guild
    by definition.
    """
    trace = _current_trace.get()
    if not trace:
        return False
    if guild_id and not trace.guild_id and log_type in constants.REPORTED_EVENT_TYPES:
        trace.guild_id = str(guild_id)
    trace.add(message, levelno, log_type=log_type)
    return True


def has_open_trace() -> bool:
    return _current_trace.get() is not None

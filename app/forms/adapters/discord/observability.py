"""Every decision becomes analytics, journey lines, logs and metrics, from one seam."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from prometheus_client import Counter, Gauge

from app import logger
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.forms.engine.decide import Decision
from app.forms.engine.session import FormSession
from app.services import analytics, journey
from app.services.analytics import bucket_duration
from app.services.trace import current_trace

REJECTED = Counter(
    "keiko_form_events_rejected_total", "Events decide refused", ["reason"]
)
EFFECT_FAILURES = Counter(
    "keiko_form_effect_failures_total", "Effects that raised", ["effect"]
)
SESSIONS = Counter(
    "keiko_form_sessions_total",
    "Sessions by feature and final status",
    ["feature", "status"],
)
LOOP_LAG = Gauge("keiko_event_loop_lag_seconds", "How late the event loop wakes up")

OPENED = ("feature.setup_opened", "feature.manager_opened")

TERMINAL = (
    "setup.completed",
    "setup.discarded",
    "setup.abandoned",
    "setup.discard_recovered",
)


@dataclass
class Friction:
    """What one session went through, counted from its decisions."""

    started_at: float = field(default_factory=time.monotonic)
    steps_viewed: int = 0
    back_count: int = 0
    validation_failures: int = 0
    viewed_at: dict[str, float] = field(default_factory=dict)

    def observe(self, name: str, props: Mapping[str, Any] | None = None) -> None:
        """Count one product event."""
        step_key = str((props or {}).get("step_key") or "")
        if name == "setup.step_viewed":
            self.steps_viewed += 1
            self.viewed_at[step_key] = time.monotonic()
        elif name == "setup.step_back":
            self.back_count += 1
        elif name in ("setup.validation_failed", "setup.required_missing"):
            self.validation_failures += 1

    def time_on(self, step_key: str) -> int | None:
        """Milliseconds since `step_key` was shown, when it was."""
        shown = self.viewed_at.pop(step_key, None)
        if shown is None:
            return None
        return int((time.monotonic() - shown) * 1000)

    def props(self) -> dict[str, Any]:
        """The numbers every terminal setup event reports the same way."""
        duration = int((time.monotonic() - self.started_at) * 1000)
        return {
            "steps_viewed": self.steps_viewed,
            "validation_failures": self.validation_failures,
            "back_count": self.back_count,
            "duration_ms": duration,
            "duration_bucket": bucket_duration(duration),
        }


def open_journey(session: FormSession, name: str, source: str, is_admin: bool) -> None:
    """Start the single log message that follows this session to its end."""
    trace = current_trace()
    if trace is not None:
        trace.session_id = session.id
    journey.open_journey(
        session.id,
        name,
        guild_id=session.origin.guild_id,
        user_id=session.origin.user_id,
        feature=session.key,
        source=source,
        inherit=trace,
        is_admin=is_admin,
    )


def emit(
    decision: Decision,
    session: FormSession,
    source: str,
    friction: Friction,
    is_admin: bool | None = None,
) -> None:
    """Emit the decision's product events with the session's identity."""
    root = session.id if session.parent_id is None else session.parent_id
    for name, props in decision.analytics:
        friction.observe(name, props)
        extra: dict[str, Any] = dict(props)
        if name == "setup.step_completed":
            elapsed = friction.time_on(str(props.get("step_key") or ""))
            if elapsed is not None:
                extra["ms_on_step"] = elapsed
        if name in TERMINAL:
            extra.update(friction.props())
        if name in OPENED and is_admin is not None:
            extra["is_admin"] = is_admin
        analytics.emit(
            name,
            guild_id=session.origin.guild_id,
            user_id=session.origin.user_id,
            feature=session.key,
            source=source,
            session_id=root,
            **extra,
        )


def log_decision(decision: Decision, event: Any, before: FormSession) -> None:
    """One line per decision: session, revision, cursor, event, status, effects."""
    session = decision.session
    effects = ", ".join(_describe(effect) for effect in decision.effects)
    if decision.rejected:
        REJECTED.labels(reason=decision.rejected).inc()
        logger.warn(
            f"form {session.key} {session.id} rev={before.revision} "
            f"{type(event).__name__} rejected: {decision.rejected}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return
    logger.info(
        f"form {session.key} {session.id} rev={session.revision} "
        f"cursor={session.cursor} {type(event).__name__} -> {session.status.value} "
        f"effects=[{effects}]",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    for rule in decision.evaluated_rules:
        if rule.skipped:
            logger.info(
                f"form {session.key} skipped {rule.step_key} "
                f"because {rule.explanation}",
                log_type=logconstants.COMMAND_INFO_TYPE,
            )
    if session.is_closed:
        SESSIONS.labels(feature=session.key, status=session.status.value).inc()


def _describe(effect: Any) -> str:
    """An effect's name with the one detail worth a log line."""
    name = type(effect).__name__
    detail = getattr(effect, "key", None) or getattr(effect, "kind", None)
    return f"{name}({detail})" if isinstance(detail, str) else name


def log_effects(session: FormSession, outcomes: list[Any]) -> None:
    """One line per effect: name, outcome, duration."""
    for outcome in outcomes:
        if outcome.ok:
            logger.info(
                f"form {session.key} {session.id} effect {outcome.effect} ok "
                f"{outcome.duration_ms}ms",
                log_type=logconstants.COMMAND_INFO_TYPE,
            )
        else:
            EFFECT_FAILURES.labels(effect=outcome.effect).inc()
            logger.error(
                f"form {session.key} {session.id} effect {outcome.effect} failed: "
                f"{outcome.error}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=error_context(session, effect=outcome.effect),
            )


def error_context(session: FormSession, **extra: Any) -> ErrorContext:
    """The error context of a session, with the fields the review asked for."""
    return ErrorContext(
        flow=f"form_{session.key}",
        guild_id=session.origin.guild_id,
        user_id=session.origin.user_id,
        extra={
            "session_id": session.id,
            "revision": session.revision,
            "definition_version": session.definition[1],
            "cursor": session.cursor,
            "status": session.status.value,
            **extra,
        },
    )


def close_journey(session: FormSession, outcome: str) -> None:
    """Close the session's story with `outcome`."""
    journey.finalize(
        session.id if session.parent_id is None else session.parent_id, outcome
    )


def record_loop_lag(expected_interval: float, measured: float) -> None:
    """Store how late the loop woke up compared to the interval it asked for."""
    LOOP_LAG.set(max(0.0, measured - expected_interval))

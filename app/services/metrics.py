"""Operational metrics any layer records: interactions, loop lag, the build, refusals,
and how long each outside dependency takes.

Prometheus, never product analytics (docs/analytics.md). Every label is a closed
vocabulary, and no label is ever a guild or a user id.
"""
import time
from contextlib import contextmanager
from typing import Iterator, Literal, Optional

import discord
from prometheus_client import Counter, Gauge, Histogram

from app.constants import Commands as constants
from app.constants import Dependencies

InteractionOutcome = Literal["in_time", "deferred", "failed"]

INTERACTIONS = Counter(
    "keiko_interactions_total",
    "Interactions by how they were answered, with Discord's error code on a failure",
    ["outcome", "code"],
)
LOOP_LAG = Histogram(
    "keiko_event_loop_lag_seconds",
    "How late each heartbeat tick woke up against its schedule",
    buckets=constants.HEARTBEAT_LAG_BUCKETS,
)
BUILD_INFO = Gauge("keiko_build_info", "The release this process runs", ["version"])
WEBHOOK_REFUSALS = Counter(
    "keiko_webhook_refusals_total",
    "Webhook requests answered with a 4xx, by route and status",
    ["route", "status"],
)
DEPENDENCY_LATENCY = Histogram(
    "keiko_dependency_latency_seconds",
    "How long each call to an outside dependency took, answered or not",
    ["dependency"],
    buckets=Dependencies.LATENCY_BUCKETS,
)


def record_interaction(
    outcome: InteractionOutcome, error: Optional[BaseException] = None
) -> None:
    """Count one interaction by how it was answered."""
    INTERACTIONS.labels(outcome=outcome, code=discord_error_code(error)).inc()


def discord_error_code(error: Optional[BaseException]) -> str:
    """The JSON error code Discord answered with, such as 10062 or 40060, or empty."""
    if isinstance(error, discord.HTTPException) and error.code:
        return str(error.code)
    return ""


def record_loop_lag(seconds_late: float) -> None:
    """Count one tick with how late it woke up; an early one counts as on time."""
    LOOP_LAG.observe(max(0.0, seconds_late))


def record_build_info(version: str) -> None:
    """Expose the running release as `keiko_build_info{version}`."""
    BUILD_INFO.labels(version=version).set(1)


def record_webhook_refusal(route: str, status: int) -> None:
    """Count one webhook request refused with `status`; `route` is the rule, never a path."""
    WEBHOOK_REFUSALS.labels(route=route, status=str(status)).inc()


def record_dependency_latency(dependency: str, seconds: float) -> None:
    """Count one call to `dependency` with how long it took."""
    _latency_of(dependency).observe(max(0.0, seconds))


@contextmanager
def timed(dependency: str) -> Iterator[None]:
    """Time the block as one call to `dependency`, whether it answered or raised."""
    latency = _latency_of(dependency)
    started = time.perf_counter()
    try:
        yield
    finally:
        latency.observe(time.perf_counter() - started)


def _latency_of(dependency: str):
    if dependency not in Dependencies.NAMES:
        raise ValueError(f"{dependency!r} is not one of the dependencies Keiko names")
    return DEPENDENCY_LATENCY.labels(dependency=dependency)

"""Operational metrics any layer records: interactions, loop lag, the build.

Prometheus, never product analytics (docs/analytics.md). Every label is a closed
vocabulary, and no label is ever a guild or a user id.
"""
from typing import Literal, Optional

import discord
from prometheus_client import Counter, Gauge, Histogram

from app.constants import Commands as constants

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


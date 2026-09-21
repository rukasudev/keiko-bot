"""One configuration session, one log message, edited until it ends.

A trace covers a single interaction. A journey covers everything the person did
in one attempt at configuring a feature — which spans many interactions — so it
is the same `Trace` structure kept open and re-rendered instead of closed.

Lines come from the product events that are already emitted, through
`analytics.register_observer`. Nothing here is instrumented separately, so a
line in the log can never disagree with a number in a dashboard.
Reference: docs/analytics.md
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.constants import Commands as constants
from app.services.trace import Trace

_JOURNEYS: Dict[str, Trace] = {}
_PUBLISHER: Optional[Callable[[Trace], None]] = None
_RECOVERY_LISTENER: Optional[Callable[[str], None]] = None

OUTCOME_ICONS = {
    # The one vocabulary for "what happened". The log embed titles read from
    # here rather than keeping their own copy, which is how `edited` once ended
    # up as 📝 in one place and 🔧 in another.
    "added": "➕",
    "removed": "➖",
    "enabled": "🎉",
    "saved": "✅",
    "recovered": "✅",
    "edited": "🔧",
    "discarded": "🚫",
    "abandoned": "⌛",
    "disabled": "🛑",
    "paused": "⏸️",
    "resumed": "▶️",
    "failure": "❌",
    "in progress": "⏳",
}

HISTORY_ICONS = {
    "completed": "✅",
    "discarded": "🚫",
    "expired": "⌛",
    "open": "⏳",
}


def _line_for(event: str, props: Dict[str, Any], feature: str = None) -> Optional[str]:
    """Event name and properties to the sentence a human reads.

    Step keys are YAML identifiers — `custom_links` means nothing to a reader —
    so they are resolved to the title the user actually saw on screen.
    """
    from app.services.utils import describe_step

    step = describe_step(feature, props.get("step_key")) if feature else props.get("step_key")

    lines = {
        "feature.setup_opened": "setup opened",
        "feature.manager_opened": "manager opened",
        "setup.step_viewed": f"step: {step} ({props.get('step_action')})",
        "setup.step_back": f"↩️ went back from `{step}`",
        "setup.validation_failed": (
            f"⚠️ `{step}` rejected — {props.get('error_key')}"
            f" (try {props.get('attempt_n', 1)})"
        ),
        "setup.required_missing": (
            f"⚠️ confirmed with {props.get('missing_count')} required field(s) empty"
        ),
        "setup.discard_recovered": "↩️ chose to keep the setup",
        "setup.completed": _saved(props, feature),
        "setup.discarded": f"🚫 discarded at `{step}`",
        "setup.abandoned": f"⌛ expired at `{step}`",
        "config.changed": f"🔧 edited — {_changed(props)}",
        "feature.enabled": "🟢 feature enabled",
        "feature.paused": "⏸️ feature paused",
        "feature.unpaused": "▶️ feature resumed",
        "feature.disabled": "🛑 feature disabled",
        "feature.item_added": "➕ item added",
        "feature.item_removed": "➖ item removed",
        "feature.tested": f"👁️ tested via {props.get('surface')}",
        "value.blocked_by_permission": "🚷 Discord refused the action",
        "command.failed": f"❌ {props.get('error_type')}",
        "feature.commit_failed": (
            f"❌ {props.get('commit_kind')} failed at `{step}`:"
            f" {props.get('error_type')}"
        ),
    }
    return lines.get(event)


def _saved(props: Dict[str, Any], feature: str = None) -> str:
    """What a save configured: step titles and counts, never the values."""
    from app.services.utils import describe_step

    parts = []
    steps = props.get("configured_steps")
    if isinstance(steps, (list, tuple)) and steps:
        parts.append(", ".join(
            describe_step(feature, key) if feature else str(key) for key in steps
        ))
    items = props.get("item_count")
    if isinstance(items, int) and not isinstance(items, bool) and items:
        parts.append(f"{items} item" if items == 1 else f"{items} items")
    timing = f"{props.get('steps_viewed')} steps, {props.get('duration_bucket')}"
    if not parts:
        return f"✅ saved ({timing})"
    return "✅ saved: " + " · ".join(parts + [timing])


def _changed(props: Dict[str, Any]) -> str:
    """Which settings changed — the names only, never the values."""
    keys = props.get("changed_keys")
    if isinstance(keys, (list, tuple)) and keys:
        return ", ".join(f"`{key}`" for key in keys)
    count = props.get("changed_count")
    return f"{count} field(s)" if count else "no field"


TERMINAL_OUTCOMES = {
    "feature.commit_failed": "failure",
    "setup.completed": "saved",
    "setup.discarded": "discarded",
    "setup.abandoned": "abandoned",
    "config.changed": "edited",
    "feature.disabled": "disabled",
    "feature.paused": "paused",
    "feature.unpaused": "resumed",
}

# Steps, not endings: a manager exists to add more than one. They name the last
# action so the log embed can be titled by it, without closing the session.
ACTIONS = {
    "feature.item_added": "added",
    "feature.item_removed": "removed",
    "feature.enabled": "enabled",
}


def set_publisher(publisher: Optional[Callable[[Trace], None]]) -> None:
    """The logger registers how a journey reaches Discord."""
    global _PUBLISHER
    _PUBLISHER = publisher


def set_recovery_listener(listener: Optional[Callable[[str], None]]) -> None:
    """The logger registers how the errors of someone who carried on are marked."""
    global _RECOVERY_LISTENER
    _RECOVERY_LISTENER = listener


def recovery_key(guild_id: Optional[str], user_id: Optional[str], flow: Optional[str]) -> str:
    """The same person using the same feature in the same server."""
    return f"{guild_id}:{user_id}:{flow}"


def recovered(key: str) -> None:
    """Someone used a feature cleanly, so what failed for them before did not stop them."""
    if _RECOVERY_LISTENER is not None:
        _RECOVERY_LISTENER(key)


LINE_KINDS = {"setup.step_viewed": "step", "setup.step_back": "step"}
CONDENSED_OUTCOMES = frozenset({"saved", "edited"})
FAILURE_EVENTS = frozenset({"command.failed", "feature.commit_failed"})


def _condense(story: Trace) -> None:
    """A saved session is read for what it configured, not step by step."""
    story.lines = [line for line in story.lines if line.get("kind") != "step"]
    story.truncated = 0


def get(session_id: str) -> Optional[Trace]:
    return _JOURNEYS.get(session_id)


def clear() -> None:
    _JOURNEYS.clear()


def open_journey(
    session_id: str,
    name: str,
    *,
    guild_id: Any = None,
    user_id: Any = None,
    feature: str = None,
    source: str = None,
    inherit: Optional[Trace] = None,
    is_admin: Optional[bool] = None,
) -> Trace:
    """Start (or reuse) the message that will follow this session to its end.

    A sub-form shares the session id, so opening again returns the journey that
    is already running rather than starting a second message.
    """
    existing = _JOURNEYS.get(session_id)
    if existing:
        return existing

    journey = Trace(
        name,
        source=source,
        guild_id=guild_id,
        user_id=user_id,
        feature=feature,
        session_id=session_id,
        is_admin=is_admin,
    )
    journey.result = "in progress"
    journey.is_journey = True

    if inherit:
        inherit.supersede(journey)

    journey.footnote = recent_attempts(guild_id, feature, session_id)
    _JOURNEYS[session_id] = journey
    _publish(journey)
    return journey


def record(envelope: Dict[str, Any]) -> None:
    """Observer entry point: turn an emitted event into a line, if it has one."""
    journey = _JOURNEYS.get(envelope.get("session_id"))
    if not journey or journey.finished_at:
        return

    line = _line_for(
        envelope["event"], envelope.get("props") or {}, envelope.get("feature")
    )
    if not line:
        return

    # The sentences carry their own icon, and a rejected value is ordinary
    # friction — only a real failure should colour the whole message red.
    failed = envelope["event"] in FAILURE_EVENTS
    level = logging.ERROR if failed else logging.INFO
    outcome = TERMINAL_OUTCOMES.get(envelope["event"])
    if outcome in CONDENSED_OUTCOMES:
        _condense(journey)
    journey.add(
        line, level, timestamp=envelope.get("ts"), kind=LINE_KINDS.get(envelope["event"])
    )

    action = ACTIONS.get(envelope["event"])
    if action:
        journey.last_action = action

    if outcome:
        finalize(envelope["session_id"], outcome)
        return

    # The message is not rewritten on every step: the edit bucket is per
    # channel, and every session shares one. It is published when the session
    # opens, when it ends, and immediately on a failure — the refresh button
    # covers anyone who wants the middle of the story before it is over.
    if failed:
        _publish(journey)


def finalize(session_id: str, outcome: str) -> Optional[Trace]:
    """Close the message. Safe to call twice — a timeout fires even after a save."""
    journey = _JOURNEYS.get(session_id)
    if not journey or journey.finished_at:
        return journey

    journey.finish(outcome)
    _publish(journey)
    _JOURNEYS.pop(session_id, None)
    return journey


def rebuild(session_id: str) -> Optional[Trace]:
    """Rebuild a session's story from the events, for the refresh button.

    The steps were never only in memory — every line came from an event that is
    already stored. So refreshing costs one read and works on any message,
    including one left stale by a restart.
    """
    from app.services import analytics_reports

    events = analytics_reports.session_events(session_id)
    if not events:
        return _JOURNEYS.get(session_id)

    first = events[0]
    story = Trace(
        first.get("props", {}).get("command") or first.get("feature") or "session",
        source=first.get("source"),
        guild_id=first.get("guild_id"),
        user_id=first.get("user_id"),
        feature=first.get("feature"),
        session_id=session_id,
    )
    story.is_journey = True
    story.started_at = first.get("ts") or story.started_at
    story.is_admin = next(
        (
            envelope["props"]["is_admin"]
            for envelope in events
            if "is_admin" in (envelope.get("props") or {})
        ),
        None,
    )

    outcome = None
    entries = []
    for envelope in events:
        line = _line_for(
            envelope["event"], envelope.get("props") or {}, envelope.get("feature")
        )
        if line:
            level = logging.ERROR if envelope["event"] in FAILURE_EVENTS else logging.INFO
            entries.append(
                (line, level, envelope.get("ts"), LINE_KINDS.get(envelope["event"]))
            )
        outcome = TERMINAL_OUTCOMES.get(envelope["event"], outcome)

    for line, level, timestamp, kind in entries:
        story.add(line, level, timestamp=timestamp, kind=kind)

    if outcome in CONDENSED_OUTCOMES:
        _condense(story)

    live = _JOURNEYS.get(session_id)
    story.name = live.name if live else story.name
    story.footnote = live.footnote if live else recent_attempts(
        story.guild_id, story.feature, session_id
    )

    if outcome:
        story.finished_at = events[-1].get("ts") or story.started_at
        story.result = outcome
    else:
        story.result = _open_or_expired(events[-1].get("ts"))

    return story


def _open_or_expired(last_seen: Any) -> str:
    """A session whose view has expired can never finish, whatever the message
    still says — a restart in the middle is the usual reason it says the wrong
    thing."""
    from app.constants import ViewConstants as view_constants

    if not isinstance(last_seen, datetime):
        return "in progress"
    reference = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc)
    quiet = (datetime.now(timezone.utc) - reference).total_seconds()
    return "abandoned" if quiet > view_constants.LONG_TIMEOUT_SECONDS else "in progress"


def recent_attempts(guild_id: Any, feature: str, session_id: str) -> Optional[str]:
    """What this guild already tried on this feature, read once at open time."""
    if not guild_id or not feature:
        return None

    try:
        from app.services import analytics_reports

        since = datetime.now(timezone.utc) - timedelta(
            seconds=constants.ANALYTICS_ATTEMPTS_WINDOW_SECONDS
        )
        previous = [
            session for session in analytics_reports.setup_sessions(feature)
            if session["guild_id"] == str(guild_id)
            and session["session_id"] != session_id
            and _after(session.get("last_seen"), since)
        ]
    except Exception:
        return None

    if not previous:
        return None

    from app.services.utils import describe_step

    lines = [f"**Last 24h · {feature} · this guild**"]
    for session in previous[: constants.ANALYTICS_JOURNEY_HISTORY_LIMIT]:
        icon = HISTORY_ICONS.get(session["outcome"], "•")
        stamp = session["last_seen"].strftime("%H:%M") if session.get("last_seen") else "--:--"
        detail = (
            f"at `{describe_step(feature, session['last_step'])}`"
            if session.get("last_step") else ""
        )
        lines.append(f"`{stamp}` {icon} {session['outcome']} {detail}".rstrip())

    return "\n".join(lines)


def _after(moment: Any, since: datetime) -> bool:
    if not isinstance(moment, datetime):
        return False
    reference = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return reference >= since


def _publish(journey: Trace) -> None:
    if not _PUBLISHER:
        return
    try:
        _PUBLISHER(journey)
    except Exception:
        pass


def install() -> None:
    """Wire the journey to the event stream. Called once at startup."""
    from app.services import analytics

    analytics.register_observer(record)


def outcome_icon(outcome: Optional[str]) -> str:
    return OUTCOME_ICONS.get(outcome, "•")

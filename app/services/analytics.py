"""Product analytics: one public entry point, `emit`.

Handlers never talk to a database. They call `emit`, which validates the name
against app/analytics/catalog.yml, drops anything the catalog does not declare,
strips free text, and hands a small envelope to a thread-safe queue. A loop in
AnalyticsCog drains that queue into Mongo. Losing an event is acceptable by
design; breaking a command never is. Reference: docs/analytics.md
"""
import os
import queue
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import yaml

from app.constants import Commands as constants
from app.services.trace import current_trace

_CATALOG: Optional[Dict[str, Any]] = None
_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=constants.ANALYTICS_QUEUE_MAXSIZE)
_ENABLED = True
_ENVIRONMENT = "dev"
_RECORDER: Optional[List[Dict[str, Any]]] = None
_OBSERVERS: List[Callable[[Dict[str, Any]], None]] = []
_STATS: Dict[str, int] = {
    "emitted": 0, "dropped": 0, "unknown": 0, "flushed": 0, "undeclared_props": 0,
    "observer_errors": 0,
}


def register_observer(observer: Callable[[Dict[str, Any]], None]) -> None:
    """Watch every event as it is emitted, without touching the emit sites.

    Mirrors `trace.register_sink`: one `emit` call, several consumers. The
    journey log is the first one — it renders the same events the dashboards
    count, so a line never disagrees with a number.
    """
    if observer not in _OBSERVERS:
        _OBSERVERS.append(observer)


def clear_observers() -> None:
    _OBSERVERS.clear()


def _notify(envelope: Dict[str, Any]) -> None:
    """A broken observer costs its own output, never the event or the command."""
    for observer in list(_OBSERVERS):
        try:
            observer(envelope)
        except Exception:
            _STATS["observer_errors"] += 1


def catalog() -> Dict[str, Any]:
    global _CATALOG
    if _CATALOG is None:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "analytics", "catalog.yml")
        with open(path, "r", encoding="utf-8") as handle:
            _CATALOG = yaml.safe_load(handle) or {}
    return _CATALOG


def events_catalog() -> Dict[str, Any]:
    return catalog().get("events") or {}


def sources_catalog() -> List[str]:
    return catalog().get("sources") or []


def configure(config: Any) -> None:
    global _ENABLED, _ENVIRONMENT
    _ENABLED = bool(getattr(config, "ANALYTICS_ENABLED", True))
    _ENVIRONMENT = "prod" if getattr(config, "is_prod", bool)() else "dev"


def is_enabled() -> bool:
    return _ENABLED


def stats() -> Dict[str, int]:
    return {**_STATS, "queue_depth": _QUEUE.qsize()}


def emit(event: str, **props: Any) -> Optional[Dict[str, Any]]:
    """Record one product event. Never raises, never blocks, never retries."""
    try:
        return _emit(event, props)
    except Exception:
        _STATS["dropped"] += 1
        return None


def _emit(event: str, props: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _ENABLED:
        return None

    declaration = events_catalog().get(event)
    if not declaration:
        _STATS["unknown"] += 1
        return None

    envelope = _build_envelope(event, declaration, props)

    _notify(envelope)

    if _RECORDER is not None:
        _RECORDER.append(envelope)

    try:
        _QUEUE.put_nowait(envelope)
        _STATS["emitted"] += 1
    except queue.Full:
        _STATS["dropped"] += 1

    return envelope


def _build_envelope(
    event: str, declaration: Dict[str, Any], props: Dict[str, Any]
) -> Dict[str, Any]:
    trace = current_trace()

    guild_id = props.pop("guild_id", None) or (trace.guild_id if trace else None)
    user_id = props.pop("user_id", None) or (trace.user_id if trace else None)
    feature = props.pop("feature", None) or (trace.feature if trace else None)
    source = props.pop("source", None) or (trace.source if trace else None)
    session_id = props.pop("session_id", None) or (trace.session_id if trace else None)
    result = props.pop("result", None)

    return {
        "event": event,
        "v": declaration.get("version", 1),
        "ts": datetime.now(timezone.utc),
        "guild_id": str(guild_id) if guild_id else None,
        "user_id": str(user_id) if user_id else None,
        "actor": declaration.get("actor", "user"),
        "feature": feature,
        "source": _valid_source(source),
        "session_id": session_id,
        "result": _valid_result(result),
        "props": sanitize_props(_declared_props(declaration, props)),
        "env": _ENVIRONMENT,
    }


def _declared_props(declaration: Dict[str, Any], props: Dict[str, Any]) -> Dict[str, Any]:
    """The catalog decides which properties exist; anything else is dropped."""
    allowed = set(declaration.get("required") or []) | set(declaration.get("optional") or [])
    clean = {}
    for key, value in props.items():
        if key in allowed:
            clean[key] = value
        else:
            _STATS["undeclared_props"] += 1
    return clean


def _valid_source(source: Optional[str]) -> Optional[str]:
    return source if source in sources_catalog() else None


def _valid_result(result: Optional[str]) -> Optional[str]:
    return result if result in (catalog().get("results") or []) else None


def sanitize_props(props: Dict[str, Any]) -> Dict[str, Any]:
    """Only low-cardinality scalars survive; free text never becomes a property."""
    clean: Dict[str, Any] = {}
    for key, value in props.items():
        if len(clean) >= constants.ANALYTICS_MAX_PROPS:
            break
        safe = _sanitize_value(value)
        if safe is not None:
            clean[key] = safe
    return clean


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return value
    if isinstance(value, str):
        return value[: constants.ANALYTICS_MAX_VALUE_LENGTH]
    if isinstance(value, (list, tuple, set)):
        items = [
            str(item)[: constants.ANALYTICS_MAX_VALUE_LENGTH]
            for item in list(value)[: constants.ANALYTICS_MAX_PROPS]
        ]
        return items or None
    return None


def count_attempt(guild_id: Any, feature: str) -> int:
    """How many times this guild opened this feature in the last 24 hours.

    Answers "why did they run it again" on the log message you already read,
    instead of asking you to correlate two of them by hand.
    """
    if not guild_id or not feature:
        return 0

    from app.services import cache

    try:
        return cache.increment_redis_key_with_expiration(
            constants.REDIS_ANALYTICS_ATTEMPTS.format(
                guild_id=guild_id, feature=feature
            ),
            1,
            constants.ANALYTICS_ATTEMPTS_WINDOW_SECONDS,
        ) or 0
    except Exception:
        return 0


def describe_attempt(attempt: int, feature: str) -> Optional[str]:
    if attempt < 2:
        return None
    return f"⚠️ {attempt}º run of `{feature}` by this guild in the last 24h"


def resolve_source(interaction: Any) -> str:
    """Entry buttons stamp the interaction; anything unstamped came from a slash."""
    extras = getattr(interaction, "extras", None) or {}
    source = extras.get("keiko_source")
    return source if source in sources_catalog() else "slash"


def mark_source(interaction: Any, source: str) -> None:
    extras = getattr(interaction, "extras", None)
    if extras is not None:
        extras["keiko_source"] = source


def record_value(guild_id: Any, feature: str, outcome: str = "ok") -> None:
    """The feature did the thing it was configured to do.

    This is what separates a feature that is merely configured from one that is
    actually working, so it is the single most important signal Keiko records.
    """
    emit("value.delivered", guild_id=guild_id, feature=feature, outcome=outcome)


def record_permission_failure(guild_id: Any, feature: str, error: Exception) -> None:
    """Delivery that Discord refused: the bot cannot do its job here."""
    emit(
        "value.blocked_by_permission",
        guild_id=guild_id,
        feature=feature,
        error_type=type(error).__name__,
    )


def records_raw_choice(node: Any) -> bool:
    """True only when the YAML itself declares the list the value comes from.

    Privacy becomes a property of the configuration instead of a rule someone
    has to remember: a field with `options:`/`designs:` has a closed vocabulary
    and is safe to tabulate, while free text and anything resolved at runtime
    (channel and role ids) has neither and is never recorded.
    """
    if not isinstance(node, dict):
        return False
    if node.get("type") == "boolean-toggle":
        return True
    return bool(node.get("options") or node.get("designs"))


def bucket_duration(duration_ms: Optional[int]) -> Optional[str]:
    if duration_ms is None:
        return None
    minutes = duration_ms / 60000
    if minutes < 1:
        return "<1m"
    if minutes < 5:
        return "1-5m"
    if minutes < 15:
        return "5-15m"
    return ">15m"


def bucket_count(count: Optional[int]) -> Optional[str]:
    if count is None:
        return None
    if count <= 1:
        return "1"
    if count <= 5:
        return "2-5"
    if count <= 20:
        return "6-20"
    return "21+"


def bucket_size(size: Optional[int]) -> Optional[str]:
    if size is None:
        return None
    if size < 50:
        return "<50"
    if size < 500:
        return "50-500"
    if size < 5000:
        return "500-5k"
    return "5k+"


def drain(limit: int = constants.ANALYTICS_FLUSH_BATCH) -> List[Dict[str, Any]]:
    batch: List[Dict[str, Any]] = []
    while len(batch) < limit:
        try:
            batch.append(_QUEUE.get_nowait())
        except queue.Empty:
            break
    return batch


def flush() -> int:
    """Drain the queue into Mongo and Redis. Called by the AnalyticsCog loop.

    Keeps going while there is more, so a burst is not spread across ticks,
    but stops after a bounded number of batches so the bot's loop is never
    held hostage by a producer that outruns it.
    """
    from app.services import analytics_sink

    flushed = 0
    for _ in range(constants.ANALYTICS_FLUSH_MAX_BATCHES):
        batch = drain()
        if not batch:
            break
        analytics_sink.persist(batch)
        flushed += len(batch)

    _STATS["flushed"] += flushed
    return flushed


def start_recording() -> List[Dict[str, Any]]:
    global _RECORDER
    _RECORDER = []
    return _RECORDER


def stop_recording() -> None:
    global _RECORDER
    _RECORDER = None


def recorded() -> List[Dict[str, Any]]:
    return list(_RECORDER or [])


def reset() -> None:
    """Test hook: forget queued events, recordings and counters."""
    global _RECORDER, _ENABLED
    drain(limit=constants.ANALYTICS_QUEUE_MAXSIZE)
    _OBSERVERS.clear()
    _RECORDER = None
    _ENABLED = True
    for key in _STATS:
        _STATS[key] = 0

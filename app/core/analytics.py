"""Product analytics: one public entry point, `record`.

Handlers never talk to a database. They call `record`, which validates the type
against app/analytics/catalog.yml, drops anything the catalog does not declare,
strips free text, and builds one envelope. The catalog names the event's
collection: a change is written at once, activity and interactions go to a
thread-safe queue a loop in AnalyticsCog drains into Mongo, and every event is
counted by the sink. Losing a batched event is acceptable by design; breaking a
command never is. Reference: docs/analytics.md
"""
import asyncio
import contextlib
import os
import queue
import threading
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional, Set

import yaml

from app.constants import Commands as constants
from app.core.trace import current_trace, drain_runs

_CATALOG: Optional[Dict[str, Any]] = None
_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=constants.ANALYTICS_QUEUE_MAXSIZE)
_ENABLED = True
_RECORDER: Optional[List[Dict[str, Any]]] = None
_OBSERVERS: List[Callable[[Dict[str, Any]], None]] = []
_WRITES: Set["asyncio.Future[None]"] = set()
_STATS: Dict[str, int] = {
    "emitted": 0, "dropped": 0, "unknown": 0, "flushed": 0, "undeclared_props": 0,
    "observer_errors": 0, "change_failures": 0,
}


def register_observer(observer: Callable[[Dict[str, Any]], None]) -> None:
    """Watch every event as it is recorded, without touching the record sites.

    Mirrors `trace.register_sink`: one `record` call, several consumers. The
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


def collection_of(event_type: str) -> Optional[str]:
    """The collection the catalog writes an event to, `none` for a count alone."""
    return (events_catalog().get(event_type) or {}).get("collection")


def value_events() -> List[str]:
    """The events the catalog marks as a feature doing its job."""
    return [
        name for name, declaration in events_catalog().items()
        if declaration.get("counts_as_value")
    ]


def configure(config: Any) -> None:
    global _ENABLED
    _ENABLED = bool(getattr(config, "ANALYTICS_ENABLED", True))


def is_enabled() -> bool:
    return _ENABLED


def stats() -> Dict[str, int]:
    return {**_STATS, "queue_depth": _QUEUE.qsize()}


def actor(kind: str, actor_id: Any = None) -> Dict[str, Any]:
    """Who caused an event: a user, the bot or a job, and its id."""
    return {"kind": kind, "id": str(actor_id) if actor_id else None}


def record(
    event_type: str,
    *,
    guild_id: Any = None,
    cog: Optional[str] = None,
    actor: Optional[Mapping[str, Any]] = None,
    props: Optional[Mapping[str, Any]] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Record one event. Never raises, never blocks the loop, never retries."""
    try:
        return _record(
            event_type, guild_id, cog, actor, dict(props or {}), session_id
        )
    except Exception:
        _STATS["dropped"] += 1
        return None


def _record(
    event_type: str,
    guild_id: Any,
    cog: Optional[str],
    who: Optional[Mapping[str, Any]],
    props: Dict[str, Any],
    session_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not _ENABLED:
        return None

    declaration = events_catalog().get(event_type)
    if not declaration:
        _STATS["unknown"] += 1
        return None

    envelope = _build_envelope(
        event_type, declaration, guild_id, cog, who, props, session_id
    )

    _notify(envelope)

    if _RECORDER is not None:
        _RECORDER.append(envelope)

    if declaration.get("collection") == "changes":
        _write_change(dict(envelope))

    try:
        _QUEUE.put_nowait(envelope)
        _STATS["emitted"] += 1
    except queue.Full:
        _STATS["dropped"] += 1

    return envelope


def _build_envelope(
    event_type: str,
    declaration: Dict[str, Any],
    guild_id: Any,
    cog: Optional[str],
    who: Optional[Mapping[str, Any]],
    props: Dict[str, Any],
    session_id: Optional[str],
) -> Dict[str, Any]:
    trace = current_trace()
    if declaration.get("keiko_wide"):
        guild_id = None
    else:
        guild_id = guild_id or (trace.guild_id if trace else None)

    if _declares(declaration, "source"):
        props["source"] = _valid_source(
            props.get("source") or (trace.source if trace else None)
        )
    if "result" in props:
        props["result"] = _valid_result(props["result"])

    return {
        "ts": datetime.now(timezone.utc),
        "type": event_type,
        "guild_id": str(guild_id) if guild_id else None,
        "cog": cog or (trace.feature if trace else None),
        "actor": _actor(declaration, who, trace),
        "trace_id": trace.id if trace else None,
        "session_id": session_id or (trace.session_id if trace else None),
        "props": sanitize_props(_declared_props(declaration, props)),
        "v": declaration.get("version", 1),
    }


def _actor(
    declaration: Dict[str, Any], who: Optional[Mapping[str, Any]], trace: Any
) -> Dict[str, Any]:
    """The given actor, else the catalog's kind with the trace's user for a user."""
    if who is not None:
        return actor(who.get("kind") or declaration.get("actor", "user"), who.get("id"))

    kind = declaration.get("actor", "user")
    return actor(kind, trace.user_id if trace and kind == "user" else None)


def _declares(declaration: Dict[str, Any], prop: str) -> bool:
    return prop in (declaration.get("required") or []) or prop in (
        declaration.get("optional") or []
    )


def _write_change(document: Dict[str, Any]) -> None:
    """A change is written now, in a thread: joined off the loop, awaited by `written`
    on it."""
    from app.data.events import changes

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        writer = threading.Thread(target=_stored, args=(changes.insert, document))
        writer.start()
        writer.join()
        return

    write = loop.create_task(asyncio.to_thread(_stored, changes.insert, document))
    _WRITES.add(write)
    write.add_done_callback(_WRITES.discard)


def _stored(insert: Callable[[Dict[str, Any]], Any], document: Dict[str, Any]) -> None:
    try:
        insert(document)
    except Exception as error:
        _STATS["change_failures"] += 1
        from app import logger
        from app.constants import LogTypes as logconstants

        logger.warn(
            f"Could not write a {document.get('type')} change: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


async def written() -> None:
    """Wait until every change this loop handed to a thread is in the database."""
    loop = asyncio.get_running_loop()
    pending = [write for write in _WRITES if write.get_loop() is loop]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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

    from app.core import cache

    try:
        return cache.increment_redis_key_with_expiration(
            constants.REDIS_ATTEMPTS_WINDOW.format(
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


@contextlib.asynccontextmanager
async def counting_attempt(trace: Any, guild_id: Any, feature: Optional[str]) -> AsyncIterator[None]:
    """Counts this attempt in a thread while the command runs, and writes its footnote
    on the trace once the command is done, whether it finished or raised."""
    if not feature:
        yield
        return
    attempts = asyncio.ensure_future(asyncio.to_thread(count_attempt, guild_id, feature))
    try:
        yield
    finally:
        trace.footnote = describe_attempt(await _counted(attempts), feature)


async def _counted(attempts: "asyncio.Future[int]") -> int:
    try:
        return await attempts
    except Exception:
        return 0


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
    record("value.delivered", guild_id=guild_id, cog=feature, props={"outcome": outcome})


def record_permission_failure(guild_id: Any, feature: str, error: Exception) -> None:
    """Delivery that Discord refused: the bot cannot do its job here."""
    record(
        "value.blocked_by_permission",
        guild_id=guild_id,
        cog=feature,
        props={"error_type": type(error).__name__},
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
    """Drain the event queue, then the closed traces, into Mongo. Called by the
    AnalyticsCog loop.

    Keeps going while there is more, so a burst is not spread across ticks,
    but stops after a bounded number of batches so the bot's loop is never
    held hostage by a producer that outruns it.
    """
    from app.core import analytics_sink

    flushed = 0
    for _ in range(constants.ANALYTICS_FLUSH_MAX_BATCHES):
        batch = drain()
        if not batch:
            break
        analytics_sink.persist(batch)
        flushed += len(batch)

    analytics_sink.persist_runs(drain_runs())
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
    _WRITES.clear()
    _RECORDER = None
    _ENABLED = True
    for key in _STATS:
        _STATS[key] = 0

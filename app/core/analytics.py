"""Product analytics: one public entry point, `record`.

Handlers never talk to a database. They call `record`, which validates the type
against app/analytics/catalog.yml, drops anything the catalog does not declare,
strips free text, and builds one envelope. The catalog names the event's
collection: a change is written at once, activity and interactions go to a
thread-safe queue a loop in AnalyticsCog drains into Mongo, and every event is
counted by the analytics writer. Losing a batched event is acceptable by design; breaking a
command never is. Reference: docs/analytics.md
"""
import asyncio
import contextlib
import queue
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import (
    Annotated, Any, AsyncIterator, Callable, Dict, List, Literal, Mapping, Optional, Set,
    Tuple, Union,
)

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, StringConstraints,
    model_validator,
)

from app.constants import Commands as constants
from app.core.commands import Open, strict_yaml
from app.core.trace import current_trace, drain_runs
from app.core.threads import to_thread

CATALOG_FILE = Path(__file__).resolve().parent.parent / "analytics" / "catalog.yml"
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


Collection = Literal["changes", "activity", "interactions", "none"]
ActorKind = Literal["user", "bot", "job"]
Scope = Literal["server", "keiko"]
Timestamp = Literal["$ts"]
PropValue = Union[StrictBool, StrictInt, StrictStr]
Step = Annotated[StrictInt, Field(gt=0)]
Prose = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CatalogNode(BaseModel):
    """Base of every catalog model: frozen, refusing a field it does not declare."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Lifetime(CatalogNode):
    """How one event advances its server's `analytics.lifetime` document."""

    counts_admin: StrictBool = Field(
        default=False,
        description="Keeps the event's actor among the server's admins, counted in "
        "`admins_count`.",
        json_schema_extra={"role": "behavior"},
    )
    when: Open[PropValue] = Field(
        default_factory=dict,
        description="Applies the block only when every listed prop of the event equals "
        "its value.",
        examples=[{"channel_found": True}],
        json_schema_extra={"role": "conditions"},
    )
    inc: Open[Step] = Field(
        default_factory=dict,
        description="Adds the step to each counter path; `{feature}` is the event's cog, "
        "and a path naming it is skipped for an event with none.",
        examples=[{"totals.errors": 1, "features.{feature}.config_changes": 1}],
        json_schema_extra={"role": "data"},
    )
    set: Open[Optional[Timestamp]] = Field(
        default_factory=dict,
        description="Writes each path: `$ts` the event's time, `null` clears "
        "the field.",
        examples=[{"features.{feature}.paused_at": "$ts", "removed_at": None}],
        json_schema_extra={"role": "data"},
    )
    min: Open[Timestamp] = Field(
        default_factory=dict,
        description="Keeps the earliest event time at each path.",
        examples=[{"joined_at": "$ts"}],
        json_schema_extra={"role": "data"},
    )
    max: Open[Timestamp] = Field(
        default_factory=dict,
        description="Keeps the latest event time at each path.",
        examples=[{"last_value_at": "$ts"}],
        json_schema_extra={"role": "data"},
    )
    removed_props: Open[StrictStr] = Field(
        default_factory=dict,
        description="Names the `guild.removed` prop that reports a path this block writes: "
        "its count for an `inc` path, whether it is set for any other.",
        examples=[{"setups_completed": "totals.setups_completed"}],
        json_schema_extra={"role": "data"},
    )

    @model_validator(mode="after")
    def _reports_what_it_writes(self) -> "Lifetime":
        written = self.written()
        for prop, path in self.removed_props.items():
            if path not in written or "{feature}" in path:
                raise ValueError(
                    f"removed prop {prop!r} reports {path!r}, which this block does not "
                    "write once per server"
                )
        return self

    def operations(self) -> Tuple[Tuple[str, Mapping[str, Any]], ...]:
        """Each update operator the block declares and the paths it writes."""
        return (("inc", self.inc), ("set", self.set), ("min", self.min), ("max", self.max))

    def written(self) -> Set[str]:
        """Every path the block writes, whatever the operator."""
        return {path for _operator, paths in self.operations() for path in paths}


class Tally(CatalogNode):
    """A tally of Keiko's own the event adds to, split by the value of one prop."""

    name: StrictStr = Field(
        description="Names the tally: the field it counts and the first part of its "
        "`{name}:total` and `{name}:{label}:{value}` counter ids.",
        examples=["blocked"],
        json_schema_extra={"role": "identity"},
    )
    by: StrictStr = Field(
        description="Names the prop, one the event declares, whose value gets its own "
        "counter.",
        examples=["host"],
        json_schema_extra={"role": "data"},
    )
    label: StrictStr = Field(
        description="Says what the prop's value is inside the per-value counter id.",
        examples=["site"],
        json_schema_extra={"role": "data"},
    )


class EventDeclaration(CatalogNode):
    """One event `record` accepts: where it lands, who causes it and what it carries."""

    version: StrictInt = Field(
        ge=1,
        description="Versions the event's props; every envelope carries it as `v`.",
        examples=[1],
        json_schema_extra={"role": "identity"},
    )
    collection: Collection = Field(
        description="Says where the event lands: `changes` written at once and kept, "
        "`activity` and `interactions` in the writer's batch for their TTL, `none` "
        "counted with no document.",
        json_schema_extra={"role": "data"},
    )
    description: Prose = Field(
        description="Says what happened, for whoever reads the data; no code reads it.",
        json_schema_extra={"role": "identity"},
    )
    recorded_in: Prose = Field(
        description="Points to where the code records the event; every `app/...` path "
        "it cites must be a file of the repository.",
        examples=["app/core/events.py — Events.on_guild_join"],
        json_schema_extra={"role": "identity"},
    )
    actor: ActorKind = Field(
        description="Gives the actor kind when the call names none; a `user` actor gets "
        "the trace's user id.",
        json_schema_extra={"role": "data"},
    )
    props: Tuple[StrictStr, ...] = Field(
        default=(),
        description="Lists the props the event may carry; `record` drops any other and "
        "counts it in `undeclared_props`, and none of them is guaranteed present.",
        examples=[["command", "source", "is_admin"]],
        json_schema_extra={"role": "data"},
    )
    private_props: Tuple[StrictStr, ...] = Field(
        default=(),
        description="Keeps these props in the database and out of the monthly archive; "
        "the edge case it serves is a value a member wrote, such as a link's address.",
        examples=[["link"]],
        json_schema_extra={"role": "behavior"},
    )
    counts_as_value: StrictBool = Field(
        default=False,
        description="Marks the feature doing its job: the reports count the event as "
        "delivered value and the writer advances the server's value fields with it, "
        "so the event writes no `lifetime` of its own.",
        json_schema_extra={"role": "behavior"},
    )
    scope: Scope = Field(
        default="server",
        description="Says what the event is about: `server` names the call's or the "
        "trace's server, `keiko` is Keiko itself and names no server whatever the "
        "trace's; only `keiko.settings_changed` is `keiko`.",
        json_schema_extra={"role": "data"},
    )
    tally: Optional[Tally] = Field(
        default=None,
        description="Adds the event to a tally of Keiko's own in `analytics.keiko`; only "
        "`link.blocked` keeps one, by site.",
        examples=[{"name": "blocked", "by": "host", "label": "site"}],
        json_schema_extra={"role": "behavior"},
    )
    lifetime: Optional[Lifetime] = Field(
        default=None,
        description="Says how the writer advances the server's `analytics.lifetime` "
        "with the event.",
        json_schema_extra={"role": "behavior"},
    )

    @model_validator(mode="after")
    def _consistent(self) -> "EventDeclaration":
        props = set(self.props)
        refused = [
            (not set(self.private_props) <= props,
             "keeps private a prop it does not declare"),
            (bool(self.private_props) and self.collection == "none",
             "keeps a prop private but writes no document"),
            (self.tally is not None and self.tally.by not in props,
             "tallies a prop it does not declare"),
            (self.lifetime is not None and not set(self.lifetime.when) <= props,
             "advances its lifetime on a prop it does not declare"),
            (self.lifetime is not None and self.counts_as_value,
             "writes a lifetime and counts as value, whose fields the writer advances"),
            (self.lifetime is not None and self.scope == "keiko",
             "writes a lifetime but names no server"),
        ]
        reasons = [reason for broken, reason in refused if broken]
        if reasons:
            raise ValueError("; ".join(reasons))
        return self


class Catalog(CatalogNode):
    """What app/analytics/catalog.yml declares: every event `record` accepts."""

    sources: Tuple[StrictStr, ...] = Field(
        description="Lists every value the `source` prop may take; any other becomes "
        "null, and an interaction no entry button stamped reads as `slash`.",
        examples=[["slash", "setup_dashboard", "manager"]],
        json_schema_extra={"role": "data"},
    )
    events: Open[EventDeclaration] = Field(
        description="Declares every event `record` accepts under its `<domain>.<fact>` "
        "name; `record` drops any other name and counts it in `unknown`.",
        json_schema_extra={"role": "structure"},
    )


@cache
def catalog() -> Catalog:
    """The catalog, read strictly once and checked against its schema."""
    name = str(CATALOG_FILE)
    return Catalog.model_validate(strict_yaml(CATALOG_FILE.read_text(encoding="utf-8"), name))


def events_catalog() -> Mapping[str, EventDeclaration]:
    """Every declared event under its name."""
    return catalog().events


def sources_catalog() -> Tuple[str, ...]:
    """Every value the `source` prop may take."""
    return catalog().sources


def lifetime_snapshot() -> Dict[str, Tuple[str, bool]]:
    """Each lifetime field `guild.removed` reports: its prop, path and whether it counts."""
    fields: Dict[str, Tuple[str, bool]] = {}
    for declaration in events_catalog().values():
        rules = declaration.lifetime
        if rules is None:
            continue
        for prop, path in rules.removed_props.items():
            fields[prop] = (path, path in rules.inc)
    return fields


def value_events() -> List[str]:
    """The events the catalog marks as a feature doing its job."""
    return [
        name for name, declaration in events_catalog().items()
        if declaration.counts_as_value
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
    declaration = events_catalog().get(event_type)
    if declaration is None:
        _STATS["unknown"] += 1
        return None

    is_change = declaration.collection == "changes"
    if not _ENABLED and not is_change:
        return None

    envelope = _build_envelope(
        event_type, declaration, guild_id, cog, who, props, session_id
    )
    if is_change:
        _write_change(dict(envelope))
    if not _ENABLED:
        return envelope

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
    event_type: str,
    declaration: EventDeclaration,
    guild_id: Any,
    cog: Optional[str],
    who: Optional[Mapping[str, Any]],
    props: Dict[str, Any],
    session_id: Optional[str],
) -> Dict[str, Any]:
    trace = current_trace()
    if declaration.scope == "keiko":
        guild_id = None
    else:
        guild_id = guild_id or (trace.guild_id if trace else None)

    if "source" in declaration.props:
        props["source"] = _valid_source(
            props.get("source") or (trace.source if trace else None)
        )

    return {
        "ts": datetime.now(timezone.utc),
        "type": event_type,
        "guild_id": str(guild_id) if guild_id else None,
        "cog": cog or (trace.feature if trace else None),
        "actor": _actor(declaration, who, trace),
        "trace_id": trace.id if trace else None,
        "session_id": session_id or (trace.session_id if trace else None),
        "props": sanitize_props(_declared_props(declaration, props)),
        "v": declaration.version,
    }


def _actor(
    declaration: EventDeclaration, who: Optional[Mapping[str, Any]], trace: Any
) -> Dict[str, Any]:
    """The given actor, else the catalog's kind with the trace's user for a user."""
    if who is not None:
        return actor(who.get("kind") or declaration.actor, who.get("id"))

    kind = declaration.actor
    return actor(kind, trace.user_id if trace and kind == "user" else None)


def _write_change(document: Dict[str, Any]) -> None:
    """A change is written now: on the running loop as a task `written` awaits, and from
    a thread through the client's loop."""
    from app.data import wait

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        wait(_stored(document))
        return

    write = loop.create_task(_stored(document))
    _WRITES.add(write)
    write.add_done_callback(_WRITES.discard)


async def _stored(document: Dict[str, Any]) -> None:
    from app.data import data

    try:
        await data().events_changes.insert(document)
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
    """Wait until every change this loop is writing is in the database."""
    loop = asyncio.get_running_loop()
    pending = [write for write in _WRITES if write.get_loop() is loop]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _declared_props(declaration: EventDeclaration, props: Dict[str, Any]) -> Dict[str, Any]:
    """The catalog decides which properties exist; anything else is dropped."""
    allowed = set(declaration.props)
    clean = {}
    for key, value in props.items():
        if key in allowed:
            clean[key] = value
        else:
            _STATS["undeclared_props"] += 1
    return clean


def _valid_source(source: Optional[str]) -> Optional[str]:
    return source if source in sources_catalog() else None


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
    attempts = asyncio.ensure_future(to_thread(count_attempt, guild_id, feature))
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
    has to remember: a field with `options:` has a closed vocabulary
    and is safe to tabulate, while free text and anything resolved at runtime
    (channel and role ids) has neither and is never recorded.
    """
    if not isinstance(node, dict):
        return False
    if node.get("type") == "boolean_toggle":
        return True
    return bool(node.get("options"))


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


async def flush() -> int:
    """Drain the event queue, then the closed traces, into Mongo. Called by the
    AnalyticsCog loop.

    Keeps going while there is more, so a burst is not spread across ticks,
    but stops after a bounded number of batches so the bot's loop is never
    held hostage by a producer that outruns it.
    """
    from app.core import analytics_writer

    flushed = 0
    for _ in range(constants.ANALYTICS_FLUSH_MAX_BATCHES):
        batch = drain()
        if not batch:
            break
        await analytics_writer.persist(batch)
        flushed += len(batch)

    await analytics_writer.persist_runs(drain_runs())
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

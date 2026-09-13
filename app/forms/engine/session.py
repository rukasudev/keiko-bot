"""The one authoritative state of a form run, frozen and versioned.

A session never changes in place: every method returns a new session with
the revision bumped, and only `decide` calls those methods. Answers hold
machine values; the labels an admin reads are resolved at render time from
the definition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Union
from uuid import uuid4

SEEN_EVENTS_KEPT = 32


class Status(str, Enum):
    """Whether a session still accepts events, and how it ended if not."""

    ACTIVE = "active"
    AWAITING = "awaiting"
    COMMITTING = "committing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"


CLOSED: frozenset[Status] = frozenset(
    {Status.COMPLETED, Status.CANCELLED, Status.FAILED, Status.EXPIRED}
)


def _frozen(mapping: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(mapping or {}))


@dataclass(frozen=True)
class Answer:
    """The committed value of one step: the machine value and its parts."""

    raw: Any
    parts: Mapping[str, Any] = field(default_factory=lambda: _frozen({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", _frozen(self.parts))

    def part(self, key: str, default: Any = None) -> Any:
        """One part of a multi-part answer."""
        return self.parts.get(key, default)


@dataclass(frozen=True)
class Origin:
    """Who opened the session, where, and in which language."""

    guild_id: str
    user_id: str
    locale: str


@dataclass(frozen=True)
class Setup:
    """Run every step, then review and commit."""

    kind: str = "setup"


@dataclass(frozen=True)
class Edit:
    """Re-run the named steps over a saved document, then commit the change."""

    keys: tuple[str, ...]
    kind: str = "edit"


@dataclass(frozen=True)
class AddItem:
    """Run the composition steps once to append an item."""

    kind: str = "add_item"


@dataclass(frozen=True)
class EditItem:
    """Run the composition steps over the item at `index`."""

    index: int
    kind: str = "edit_item"


@dataclass(frozen=True)
class RemoveItem:
    """Pick an item of the composition and remove it."""

    kind: str = "remove_item"


@dataclass(frozen=True)
class Manage:
    """The panel over a saved document, with the lifecycle actions."""

    kind: str = "manage"


@dataclass(frozen=True)
class DryRun:
    """A setup that never commits: the preview of a definition."""

    kind: str = "dry_run"


Mode = Union[Setup, Edit, AddItem, EditItem, RemoveItem, Manage, DryRun]


@dataclass(frozen=True)
class FormSession:
    """One run of one definition by one admin."""

    id: str
    definition: tuple[str, int]
    mode: Mode
    origin: Origin
    status: Status
    cursor: str | None
    answers: Mapping[str, Answer]
    revision: int
    seen_events: tuple[str, ...]
    parent_id: str | None
    expires_at: datetime
    awaiting: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "answers", _frozen(self.answers))

    @property
    def key(self) -> str:
        """The form key of the definition this session runs."""
        return self.definition[0]

    @property
    def is_closed(self) -> bool:
        """True once the session accepts no state-changing event."""
        return self.status in CLOSED

    def raw(self, key: str, default: Any = None) -> Any:
        """The machine value answered under `key`."""
        answer = self.answers.get(key)
        return default if answer is None else answer.raw

    def values(self) -> dict[str, Any]:
        """Every answered key to its machine value, in answer order."""
        return {key: answer.raw for key, answer in self.answers.items()}

    def _next(self, **changes: Any) -> FormSession:
        return replace(self, revision=self.revision + 1, **changes)

    def with_answer(self, key: str, answer: Answer) -> FormSession:
        """The session with `key` answered, replacing any earlier answer."""
        answers = dict(self.answers)
        answers[key] = answer
        return self._next(answers=answers)

    def with_answers(self, answers: Mapping[str, Answer]) -> FormSession:
        """The session with every given answer set."""
        merged = dict(self.answers)
        merged.update(answers)
        return self._next(answers=merged)

    def without(self, keys: tuple[str, ...]) -> FormSession:
        """The session with the given answers forgotten."""
        answers = {k: v for k, v in self.answers.items() if k not in keys}
        return self._next(answers=answers)

    def at(self, cursor: str | None) -> FormSession:
        """The session moved to `cursor`, active again."""
        return self._next(cursor=cursor, status=Status.ACTIVE, awaiting=None)

    def with_status(self, status: Status, awaiting: str | None = None) -> FormSession:
        """The session in another lifecycle status."""
        return self._next(status=status, awaiting=awaiting)

    def remember(self, event_id: str) -> FormSession:
        """The session that has applied `event_id`, revision bumped."""
        seen = (*self.seen_events, event_id)[-SEEN_EVENTS_KEPT:]
        return self._next(seen_events=seen)

    def has_seen(self, event_id: str) -> bool:
        """True when `event_id` was applied to this session already."""
        return event_id in self.seen_events


def new_session(
    definition: tuple[str, int],
    mode: Mode,
    origin: Origin,
    *,
    ttl_seconds: int,
    parent_id: str | None = None,
    answers: Mapping[str, Answer] | None = None,
    now: datetime | None = None,
    session_id: str | None = None,
) -> FormSession:
    """A fresh active session at revision 0."""
    started = now or datetime.now(timezone.utc)
    return FormSession(
        id=session_id or uuid4().hex,
        definition=definition,
        mode=mode,
        origin=origin,
        status=Status.ACTIVE,
        cursor=None,
        answers=_frozen(answers),
        revision=0,
        seen_events=(),
        parent_id=parent_id,
        expires_at=started + timedelta(seconds=ttl_seconds),
    )

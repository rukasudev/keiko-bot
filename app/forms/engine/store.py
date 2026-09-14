"""Where sessions live: an interface, and the in-memory store the bot runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from app.constants import ViewConstants
from app.forms.engine.session import FormSession, Status


class StaleWrite(Exception):
    """A session was put back at a revision not above the one stored."""


class SessionStore(Protocol):
    """The place that owns sessions between two events."""

    def create(self, session: FormSession) -> None:
        """Remember a fresh session."""

    def get(self, session_id: str) -> FormSession | None:
        """The session with `session_id`, None when unknown."""

    def put(self, session: FormSession) -> None:
        """Replace a session with a newer revision of itself."""

    def remember(self, session: FormSession) -> None:
        """Keep a session whose only change is the events it has seen."""

    def due(self, now: datetime | None = None) -> tuple[FormSession, ...]:
        """Open sessions past their deadline, untouched."""

    def expire(self, now: datetime | None = None) -> tuple[FormSession, ...]:
        """Mark sessions past their deadline expired and return them."""

    def children(self, parent_id: str) -> tuple[FormSession, ...]:
        """Every session opened under `parent_id`."""


class InMemorySessionStore:
    """Sessions of one process, kept until they expire."""

    def __init__(self, ttl_seconds: int = ViewConstants.LONG_TIMEOUT_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, FormSession] = {}

    def create(self, session: FormSession) -> None:
        """Remember a fresh session."""
        self._sessions[session.id] = session

    def get(self, session_id: str) -> FormSession | None:
        """The session with `session_id`, None when unknown."""
        return self._sessions.get(session_id)

    def put(self, session: FormSession) -> None:
        """Replace a session with a newer revision of itself."""
        current = self._sessions.get(session.id)
        if current is not None and session.revision <= current.revision:
            raise StaleWrite(f"{session.id}: {session.revision} <= {current.revision}")
        self._sessions[session.id] = session

    def remember(self, session: FormSession) -> None:
        """Keep a session whose only change is the events it has seen."""
        current = self._sessions.get(session.id)
        if current is not None and session.revision != current.revision:
            raise StaleWrite(f"{session.id}: {session.revision} != {current.revision}")
        self._sessions[session.id] = session

    def due(self, now: datetime | None = None) -> tuple[FormSession, ...]:
        """Open sessions past their deadline, untouched."""
        moment = now or datetime.now(timezone.utc)
        return tuple(
            session
            for session in self._sessions.values()
            if not session.is_closed and session.expires_at <= moment
        )

    def expire(self, now: datetime | None = None) -> tuple[FormSession, ...]:
        """Mark open sessions past their deadline expired and return them."""
        expired: list[FormSession] = []
        for session in self.due(now):
            closed = session.with_status(Status.EXPIRED)
            self._sessions[session.id] = closed
            expired.append(closed)
        return tuple(expired)

    def forget_closed(self, before: datetime | None = None) -> tuple[str, ...]:
        """Drop closed sessions, only those past `before` if given, and return ids."""
        closed = [
            key
            for key, session in self._sessions.items()
            if session.is_closed and (before is None or session.expires_at <= before)
        ]
        for key in closed:
            del self._sessions[key]
        return tuple(closed)

    def children(self, parent_id: str) -> tuple[FormSession, ...]:
        """Every session opened under `parent_id`."""
        return tuple(s for s in self._sessions.values() if s.parent_id == parent_id)

    def __len__(self) -> int:
        return len(self._sessions)

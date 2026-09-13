"""What the adapter must do after a decision, as data it executes in order."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.forms.engine.screen import Screen
from app.forms.engine.session import FormSession


@dataclass(frozen=True)
class Effect:
    """Base of every effect."""


@dataclass(frozen=True)
class Render(Effect):
    """Draw `screen` on the session's message."""

    screen: Screen


@dataclass(frozen=True)
class OpenModal(Effect):
    """Open `screen` as a modal; its submit comes back as `action`:`arg`."""

    screen: Screen
    action: str = "modal"
    arg: str | None = None


@dataclass(frozen=True)
class ResumeChild(Effect):
    """A click on the parent's screen while a child is open: show the child again."""


@dataclass(frozen=True)
class ShowError(Effect):
    """Tell the admin why the value was refused, on an ephemeral message."""

    key: str
    args: Mapping[str, str] = field(default_factory=dict)
    plain: bool = False
    delete_after: int | None = 10


@dataclass(frozen=True)
class Notice(Effect):
    """A short ephemeral note that removes itself."""

    key: str


@dataclass(frozen=True)
class Confirm(Effect):
    """Ask the admin to keep or discard, on a message of its own."""

    screen: Screen


@dataclass(frozen=True)
class Dismiss(Effect):
    """Remove the message the interaction came from (a confirmation)."""


@dataclass(frozen=True)
class OpenChild(Effect):
    """Open `session` under the current one and start it."""

    session: FormSession


@dataclass(frozen=True)
class ResumeParent(Effect):
    """Hand the child's result to its parent as a `ChildFinished` event."""

    parent_id: str
    child_mode: str
    answers: Mapping[str, Any]
    index: int | None = None


@dataclass(frozen=True)
class Commit(Effect):
    """Ask the feature to write; the outcome comes back as an event."""

    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finalize(Effect):
    """Show the final state and take the controls off the message."""

    kind: str
    screen: Screen | None = None


@dataclass(frozen=True)
class RunAside(Effect):
    """Run a read-only side action: help, history, preview, a feature extra."""

    name: str


@dataclass(frozen=True)
class Ack(Effect):
    """Acknowledge the interaction and change nothing on screen."""

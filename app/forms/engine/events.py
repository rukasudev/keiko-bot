"""Everything that can happen to a session, as data the adapter creates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    """What every event carries: the interaction id and the revision seen."""

    event_id: str
    expected_revision: int | None = None


@dataclass(frozen=True)
class Started(Event):
    """The session was opened."""


@dataclass(frozen=True)
class Answered(Event):
    """The current step was confirmed, with the payload or the draft so far."""

    step_key: str = ""
    payload: Any = None


@dataclass(frozen=True)
class Drafted(Event):
    """Part of the current step changed without confirming it."""

    step_key: str = ""
    changes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SectionOpened(Event):
    """A card section's button was pressed."""

    step_key: str = ""
    index: int = 0


@dataclass(frozen=True)
class SectionReset(Event):
    """A card section went back to its default."""

    step_key: str = ""
    index: int = 0


@dataclass(frozen=True)
class PickerClosed(Event):
    """A card picker was left without choosing."""

    step_key: str = ""


@dataclass(frozen=True)
class Back(Event):
    """The previous step was asked for."""


@dataclass(frozen=True)
class Cancel(Event):
    """Cancel was pressed; the discard confirmation follows."""


@dataclass(frozen=True)
class KeepEditing(Event):
    """The admin chose to keep the setup after Cancel."""


@dataclass(frozen=True)
class DiscardConfirmed(Event):
    """The admin chose to discard the setup after Cancel."""


@dataclass(frozen=True)
class ReviewConfirmed(Event):
    """Confirm on the review screen."""


@dataclass(frozen=True)
class EditRequested(Event):
    """Edit was pressed; `target` is a step key when a section button was used."""

    target: str | None = None


@dataclass(frozen=True)
class AddRequested(Event):
    """Add was pressed on a review or a panel."""


@dataclass(frozen=True)
class RemoveRequested(Event):
    """Remove was pressed on a review or a panel."""


@dataclass(frozen=True)
class TargetChosen(Event):
    """An option of the edit or remove picker was chosen."""

    value: str = ""


@dataclass(frozen=True)
class MemberChosen(Event):
    """A member was chosen on the composition member picker."""

    user_id: str = ""


@dataclass(frozen=True)
class ItemRemoved(Event):
    """The item at `index` leaves the composition."""

    index: int = 0


@dataclass(frozen=True)
class ScreenRequested(Event):
    """Show the current screen again (a modal dismissed, a parent clicked)."""


@dataclass(frozen=True)
class ChildFinished(Event):
    """A child session completed and hands its answers back."""

    child_mode: str = ""
    answers: Mapping[str, Any] = field(default_factory=dict)
    index: int | None = None


@dataclass(frozen=True)
class Lifecycle(Event):
    """Pause, unpause or disable was pressed on the panel."""

    action: str = ""


@dataclass(frozen=True)
class LifecycleConfirmed(Event):
    """The confirmation modal of a lifecycle action was submitted."""

    action: str = ""
    word: str = ""


@dataclass(frozen=True)
class Aside(Event):
    """A read-only button was pressed: help, history, preview, a feature extra."""

    name: str = ""


@dataclass(frozen=True)
class CommitSucceeded(Event):
    """The feature wrote what the commit asked for."""

    kind: str = ""
    written: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommitFailed(Event):
    """The feature raised before finishing the commit."""

    kind: str = ""
    error: str = ""


@dataclass(frozen=True)
class Expired(Event):
    """The session outlived its deadline."""

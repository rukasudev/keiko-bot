"""A click becomes an event, and a component carries the id that says which."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.settings.form import events as ev
from app.settings.form.manager import (
    ItemRemovalRequested,
    Lifecycle,
    LifecycleConfirmed,
    OptionToggled,
    PageTurned,
    SearchRequested,
)

PREFIX = "k"


@dataclass(frozen=True)
class ComponentId:
    """What a custom_id says: which session, seen at which revision, doing what."""

    session_id: str
    revision: int
    action: str
    arg: str | None = None

    @property
    def target(self) -> str:
        """The action with its argument, as the engine names it."""
        return f"{self.action}:{self.arg}" if self.arg is not None else self.action


def encode(session_id: str, revision: int, action: str, arg: str | None = None) -> str:
    """The custom_id of a component rendered at `revision`."""
    parts = [PREFIX, session_id, str(revision), action]
    if arg is not None:
        parts.append(arg)
    return ":".join(parts)


def decode(custom_id: str | None) -> ComponentId | None:
    """The parts of a custom_id, None when it is not one of ours."""
    if not custom_id:
        return None
    parts = custom_id.split(":", 4)
    if len(parts) < 4 or parts[0] != PREFIX or not parts[2].isdigit():
        return None
    arg = parts[4] if len(parts) == 5 else None
    return ComponentId(parts[1], int(parts[2]), parts[3], arg)


def is_ours(custom_id: str | None) -> bool:
    """True when the custom_id belongs to the form platform."""
    return decode(custom_id) is not None


LIFECYCLE = ("pause", "unpause", "disable")


def event_id_of(interaction: Any) -> str:
    """The interaction id, the one thing Discord guarantees unique per click."""
    return str(interaction.id)


def to_event(
    interaction: Any,
    component: ComponentId,
    payload: Any = None,
    cursor: str | None = None,
    section: str | None = None,
) -> ev.Event:
    """The event a click, a select or a modal submit means for its session.

    Answers name their step: `confirm`, `done` and `modal` carry it in the id,
    while a chosen value (`pick`, `design`) answers the step on show, `cursor`,
    or the card `section` open in front of it.
    """
    event_id = event_id_of(interaction)
    revision = component.revision
    action, arg = component.action, component.arg
    on_one = _on_one_item(action, arg, event_id, revision)

    if on_one is not None:
        return on_one
    if action in SIMPLE:
        return SIMPLE[action](event_id, revision)
    if action in ("confirm", "modal"):
        return ev.Answered(event_id, revision, arg or "", payload)
    if action == "done":
        return ev.Answered(event_id, revision, arg or "", None)
    if action in ("pick", "design"):
        return ev.Answered(event_id, revision, section or cursor or "", arg)
    if action in ("draft", "set"):
        return ev.Drafted(
            event_id, revision, cursor or "", _drafted(action, arg, payload)
        )
    if action in SECTIONS:
        return SECTIONS[action](event_id, revision, cursor or "", int(arg or 0))
    if action in CHOSEN:
        return CHOSEN[action](event_id, revision, _first(payload))
    if action == "targets":
        return ev.TargetsChosen(event_id, revision, _every(payload))
    return _named(action, arg, payload, event_id, revision)


def _drafted(action: str, arg: str | None, payload: Any) -> dict[str, Any]:
    """What a select reported for its slot, or the one value a button sets."""
    if action == "set":
        slot, _, value = (arg or "").rpartition("=")
        return {slot: value}
    return {arg or "": payload}


def _on_one_item(
    action: str, arg: str | None, event_id: str, revision: int | None
) -> ev.Event | None:
    """What a button beside one item of a screen means, None for anything else."""
    if action == "remove_this":
        return ItemRemovalRequested(event_id, revision, arg or "")
    if action in ("turn_on", "turn_off"):
        key, _, chosen = (arg or "").rpartition("=")
        return OptionToggled(event_id, revision, key, chosen, action == "turn_on")
    if action == "page":
        number = arg or "0"
        return PageTurned(event_id, revision, int(number) if number.isdigit() else 0)
    return None


def _named(
    action: str, arg: str | None, payload: Any, event_id: str, revision: int | None
) -> ev.Event:
    if action == "edit":
        return ev.EditRequested(event_id, revision, arg)
    if action == "lifecycle":
        return Lifecycle(event_id, revision, arg or "")
    if action == "word":
        return LifecycleConfirmed(event_id, revision, arg or "", _typed(payload))
    name, _, target = (arg or action).partition(":")
    return ev.Aside(event_id, revision, name, target)


def _first(payload: Any) -> str:
    """The one value a select reported."""
    if isinstance(payload, (list, tuple)):
        return str(payload[0]) if payload else ""
    return str(payload or "")


def _every(payload: Any) -> tuple[str, ...]:
    if isinstance(payload, (list, tuple)):
        return tuple(str(value) for value in payload)
    return (str(payload),) if payload else ()


def _typed(payload: Any) -> str:
    """The one text a modal reported."""
    if isinstance(payload, dict):
        return _first(payload.get("inputs"))
    return str(payload or "")


SIMPLE: dict[str, type[ev.Event]] = {
    "back": ev.Back,
    "cancel": ev.Cancel,
    "keep": ev.KeepEditing,
    "discard": ev.DiscardConfirmed,
    "add": ev.AddRequested,
    "remove": ev.RemoveRequested,
    "picker_back": ev.PickerClosed,
    "remove_items": ev.RemovalConfirmed,
    "search": SearchRequested,
}
SECTIONS = {"section": ev.SectionOpened, "reset": ev.SectionReset}
CHOSEN = {"target": ev.TargetChosen, "member": ev.MemberChosen}

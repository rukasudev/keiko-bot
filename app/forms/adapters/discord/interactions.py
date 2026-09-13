"""From a Discord interaction to an engine event."""

from __future__ import annotations

from typing import Any

from app.forms.adapters.discord.ids import ComponentId
from app.forms.engine import events as ev

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
    if action in SIMPLE:
        return SIMPLE[action](event_id, revision)
    if action in ("confirm", "modal"):
        return ev.Answered(event_id, revision, arg or "", payload)
    if action == "done":
        return ev.Answered(event_id, revision, arg or "", None)
    if action in ("pick", "design"):
        return ev.Answered(event_id, revision, section or cursor or "", arg)
    if action == "draft":
        return ev.Drafted(event_id, revision, cursor or "", {arg or "": payload})
    if action in SECTIONS:
        return SECTIONS[action](event_id, revision, cursor or "", int(arg or 0))
    if action in CHOSEN:
        return CHOSEN[action](event_id, revision, _first(payload))
    return _named(action, arg, payload, event_id, revision)


def _named(
    action: str, arg: str | None, payload: Any, event_id: str, revision: int | None
) -> ev.Event:
    if action == "edit":
        return ev.EditRequested(event_id, revision, arg)
    if action == "lifecycle":
        return ev.Lifecycle(event_id, revision, arg or "")
    if action == "word":
        return ev.LifecycleConfirmed(event_id, revision, arg or "", _typed(payload))
    return ev.Aside(event_id, revision, arg or action)


def _first(payload: Any) -> str:
    """The one value a select reported."""
    if isinstance(payload, (list, tuple)):
        return str(payload[0]) if payload else ""
    return str(payload or "")


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
}
SECTIONS = {"section": ev.SectionOpened, "reset": ev.SectionReset}
CHOSEN = {"target": ev.TargetChosen, "member": ev.MemberChosen}

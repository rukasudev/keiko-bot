"""The custom_id codec: `k:<session>:<revision>:<action>[:<arg>]`."""

from __future__ import annotations

from dataclasses import dataclass

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

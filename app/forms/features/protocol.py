"""What a feature contributes to the platform, and nothing more.

A feature maps answers to its document and back, writes on commit, reacts
to lifecycle actions, and may add read-only side actions to its panel. It
never sees a session, never renders, and never reaches another feature's
document.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.forms.engine.screen import Button
from app.forms.engine.session import Answer
from app.forms.kinds.context import PanelRow


@dataclass(frozen=True)
class OpenContext:
    """Who opens the feature, where, and the guild objects the bot holds."""

    guild_id: str
    user_id: str
    locale: str
    guild: Any = None
    member: Any = None


@dataclass(frozen=True)
class Opened:
    """What the feature found when opened: a document to manage, or nothing."""

    document: Mapping[str, Any] | None = None
    rows: Sequence[PanelRow] | None = None
    info: str = ""
    info_title: str = ""
    extra_buttons: tuple[Button, ...] = ()
    enabled: bool = True
    previews: Mapping[str, str] = field(default_factory=dict)
    pending_previews: Awaitable[Mapping[str, str]] | None = None
    refusal: str | None = None


@dataclass(frozen=True)
class CommitContext:
    """Everything a commit may need besides its payload."""

    guild_id: str
    user_id: str
    locale: str
    document: Mapping[str, Any]
    when: datetime
    source: str = "manager"
    session_id: str | None = None


@dataclass(frozen=True)
class CommitResult:
    """What a commit wrote, so a failure midway can say what did happen."""

    written: tuple[str, ...] = ()
    external: tuple[str, ...] = ()
    document: Mapping[str, Any] | None = None


AsideHandler = Callable[[Any, Sequence[Mapping[str, Any]]], Awaitable[None]]


@dataclass(frozen=True)
class AsideAction:
    """A read-only side action of the panel or the review."""

    handler: AsideHandler
    defer: bool = False
    own_response: bool = False
    cooldown: int | None = None


class FeatureModule(Protocol):
    """The contract every feature module fulfils."""

    key: str

    async def open(self, context: OpenContext) -> Opened:
        """The saved document and panel extras, or an empty `Opened` for setup."""

    async def prefetch(
        self, step_key: str, payload: Any, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """External lookups a validator of `step_key` declared it needs."""

    def to_document(self, answers: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        """The document the answers persist as."""

    def from_document(self, document: Mapping[str, Any]) -> dict[str, Answer]:
        """The answers a saved document seeds, any schema version."""

    async def commit(
        self, kind: str, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Write what `kind` asks for and report what was written."""

    def asides(self) -> Mapping[str, AsideAction]:
        """The side actions this feature adds, by button action name."""

    def responses_for_preview(
        self, answers: Mapping[str, Answer], locale: str
    ) -> list[dict[str, Any]]:
        """The answers as the legacy preview functions read them."""

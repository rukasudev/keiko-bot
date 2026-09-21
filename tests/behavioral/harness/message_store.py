"""Deterministic in-memory model of the bot's (ephemeral) messages.

Message ids come from a monotonic counter and are exposed in normalized
output as stable aliases (M1, M2, ...) so two identical runs produce
identical output.
"""
import datetime
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import discord

from tests.behavioral.harness import normalizer

FIXED_MESSAGE_TIME = datetime.datetime(2026, 1, 1, 12, 0, 0,
                                       tzinfo=datetime.timezone.utc)

# Mirrors discord.utils.MISSING: on edits, "not passed" keeps the field and an
# explicit None REMOVES it (view=None strips the components, embed=None the
# embeds) — exactly what the real API does.
MISSING = object()


def reject_embed_on_layout_message(message: "FakeMessage", embed, content) -> None:
    """Discord refuses content and embeds on a Components V2 message, and the
    flag is fixed at send time: the edit fails with 50035 and nothing changes."""
    if not message.flags.components_v2:
        return
    if content is None and (embed is MISSING or embed is None):
        return
    raise discord.HTTPException(
        SimpleNamespace(status=400, reason="Bad Request"),
        {"code": 50035, "message": "Invalid Form Body", "errors": {
            "embeds": {"_errors": [{
                "code": "BASE_TYPE_BAD_CONTENT",
                "message": "Cannot use content or embeds with IS_COMPONENTS_V2 flag",
            }]},
        }},
    )


def _walk_view_items(view) -> List[Any]:
    """Depth-first over a view's items, containers and section accessories
    included. Local copy of locators.walk_items to keep this module a leaf."""
    items: List[Any] = []

    def walk(node) -> None:
        for item in getattr(node, "children", None) or []:
            items.append(item)
            walk(item)
            accessory = getattr(item, "accessory", None)
            if accessory is not None:
                items.append(accessory)

    if view is not None:
        walk(view)
    return items


class FakeMessage:
    def __init__(self, store: "MessageStore", message_id: int, embeds, view, ephemeral: bool):
        self.store = store
        self.id = message_id
        self.embeds = list(embeds or [])
        self.view = view
        self.ephemeral = ephemeral
        self.deleted = False
        # Fixed, deterministic timestamp: real Discord messages always carry
        # created_at, and engine code strftime()s it. Never wall-clock.
        self.created_at = FIXED_MESSAGE_TIME
        self.edited_at = None
        self.channel = SimpleNamespace(id=999, send=self._channel_send)
        self.flags = SimpleNamespace(components_v2=isinstance(view, discord.ui.LayoutView))
        # What the user can actually click: the items as they were when the
        # message was sent, exactly like discord.py's ViewStore, which maps
        # message -> item at send/edit time and reads `item.view` live at
        # dispatch. Mutating a view later does NOT change the message.
        self.registered_items = _walk_view_items(view)

    async def edit(self, *, content=None, embed=MISSING, view=MISSING, **kwargs):
        """The bot editing its own message outside any interaction (a timeout)."""
        if self.deleted:
            raise discord.NotFound(
                SimpleNamespace(status=404), {"code": 10008, "message": "Unknown Message"}
            )
        self.apply_edit(embed=embed, view=view, content=content)
        self.store.record("edit", message=self.id, embeds=self.embeds, view=self.view,
                          ephemeral=self.ephemeral)
        return self

    async def _channel_send(self, *args, **kwargs):
        self.store.record(
            "channel_send",
            content=kwargs.get("content") or (args[0] if args else None),
            embeds=[kwargs["embed"]] if kwargs.get("embed") else None,
        )

    def apply_edit(self, embed=MISSING, view=MISSING, content=None) -> None:
        reject_embed_on_layout_message(self, embed, content)
        if embed is not MISSING:
            self.embeds = [embed] if embed is not None else []
        if view is not MISSING:
            self.view = view
            self.flags.components_v2 = isinstance(view, discord.ui.LayoutView)
            # An edit that carries a view re-registers it — and view=None
            # strips the components — like the ViewStore and the real API.
            self.registered_items = _walk_view_items(view)


class MessageStore:
    def __init__(self):
        self._next_id = 1000
        self.messages: Dict[int, FakeMessage] = {}
        self.aliases: Dict[int, str] = {}
        self.events: List[Dict[str, Any]] = []
        self.pending_modal: Optional[discord.ui.Modal] = None
        # Set by the driver so every event snapshots the current form step.
        self.step_provider: Callable[[], Optional[str]] = lambda: None

    def alias(self, message_id: Optional[int]) -> Optional[str]:
        if message_id is None:
            return None
        if message_id not in self.aliases:
            self.aliases[message_id] = f"M{len(self.aliases) + 1}"
        return self.aliases[message_id]

    def create(self, embeds, view, ephemeral: bool) -> FakeMessage:
        self._next_id += 1
        message = FakeMessage(self, self._next_id, embeds, view, ephemeral)
        self.messages[message.id] = message
        self.alias(message.id)
        return message

    def record(self, kind: str, *, actor: str = "bot", message: Optional[int] = None,
               content: Optional[str] = None, embeds=None, view=None, modal=None,
               ephemeral: Optional[bool] = None, delete_after=None, **extra) -> Dict[str, Any]:
        """Record one normalized event. Snapshots are taken NOW because
        embeds/views mutate on later edits."""
        event: Dict[str, Any] = {
            "seq": len(self.events) + 1,
            "actor": actor,
            "kind": kind,
            "message": self.alias(message),
            "step": self.step_provider(),
        }
        if ephemeral is not None:
            event["ephemeral"] = ephemeral
        if content is not None:
            event["content"] = content
        if delete_after is not None:
            event["delete_after"] = delete_after
        if embeds:
            event["embed"] = normalizer.normalize_embed(embeds[0])
        if view is not None:
            event["components"] = normalizer.normalize_components(view)
            event["components_v2"] = isinstance(view, discord.ui.LayoutView)
        if modal is not None:
            event["modal"] = normalizer.normalize_modal(modal)
        event.update(extra)
        self.events.append(event)
        return event

    @property
    def current(self) -> Optional[FakeMessage]:
        """The message the user would act on next: the newest live message
        that shows components (plain notices, e.g. delete_after errors, do not
        steal focus from the actionable view). "Shows" is judged by what was
        registered at send/edit time, not by the view's current children: a
        view mutated after sending still displays its old buttons on Discord."""
        live = [m for m in self.messages.values() if not m.deleted]
        with_view = [m for m in live if m.registered_items]
        if with_view:
            return with_view[-1]
        return live[-1] if live else None

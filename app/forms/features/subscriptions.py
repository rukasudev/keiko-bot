"""What Twitch and YouTube notifications share: one subscription per item."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, cast

import app as app_module
from app.forms.engine.documents import items_of
from app.forms.engine.session import Answer
from app.forms.features.generic import GenericCogFeature
from app.forms.features.protocol import CommitContext, OpenContext

Subscribe = Callable[..., Any]


class SubscriptionFeature(GenericCogFeature):
    """A composition of subscriptions kept in sync with an external service."""

    item_key: str = ""
    external: str = ""
    subscribe: Subscribe
    unsubscribe: Subscribe

    def __init__(self, key: str) -> None:
        super().__init__(key)

    def _lookup(self, name: str) -> Any:
        bot = cast(Any, app_module).bot
        client = bot.twitch if self.external == "twitch" else bot.youtube
        lookup = (
            client.get_user_id_from_login
            if self.external == "twitch"
            else client.get_channel_id_from_username
        )
        return lookup(name)

    async def prefetch(
        self, step_key: str, payload: Any, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """The external account behind the typed name."""
        if step_key != self.item_key:
            return {}
        typed = (
            payload.get("inputs", [""])[0] if isinstance(payload, Mapping) else payload
        )
        found = await asyncio.to_thread(self._lookup, str(typed or "").lower())
        return {self.external: {self.lookup_result: found}}

    lookup_result: str = "user_id"

    def _gated(self) -> bool:
        return bool(cast(Any, app_module).bot.config.is_dev())

    def _entry(self, item: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        return self.item_document(item, locale)

    async def before_setup(
        self,
        document: dict[str, Any],
        answers: Mapping[str, Answer],
        context: CommitContext,
    ) -> dict[str, Any]:
        """Subscribe every item of a fresh setup, outside dev."""
        composition = self.definition.composition
        if self._gated() or composition is None:
            return document
        for item in items_of(document, composition.key):
            await asyncio.to_thread(self.subscribe, None, item)
        return document

    async def on_item_added(
        self, item: Mapping[str, Answer], context: CommitContext
    ) -> None:
        """A new item subscribes, outside dev."""
        if self._gated():
            return
        await asyncio.to_thread(self.subscribe, None, self._entry(item, context.locale))

    async def on_item_edited(
        self, item: Mapping[str, Answer], index: int | None, context: CommitContext
    ) -> None:
        """A changed name moves the subscription, outside dev."""
        if self._gated() or index is None:
            return
        composition = self.definition.composition
        assert composition is not None
        items = items_of(context.document, composition.key)
        old = items[index] if 0 <= index < len(items) else None
        new = self._entry(item, context.locale)
        if old is None or _name(old, self.item_key) == _name(new, self.item_key):
            return
        await asyncio.to_thread(self.unsubscribe, None, old)
        await asyncio.to_thread(self.subscribe, None, new)

    async def on_item_removed(
        self, item: Mapping[str, Any], context: CommitContext
    ) -> None:
        """A removed item unsubscribes."""
        if item:
            await asyncio.to_thread(self.unsubscribe, None, item)

    async def on_disable(self, context: CommitContext) -> None:
        """Every item unsubscribes."""
        composition = self.definition.composition
        if composition is None:
            return
        for item in items_of(context.document, composition.key):
            await asyncio.to_thread(self.unsubscribe, None, item)


def _name(item: Mapping[str, Any], key: str) -> str:
    entry = item.get(key)
    value = entry.get("value") if isinstance(entry, Mapping) else entry
    return str(value or "").lower()

"""What Twitch and YouTube notifications share: one subscription per item."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, cast

import app as app_module
from app.constants import ViewConstants as view_constants
from app.settings.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    GenericCogFeature,
    OpenContext,
)
from app.settings.form.form_state import Answer
from app.settings.form.lookups import Lookup
from app.settings.form.responses.responses import items_of, unwrap

Subscribe = Callable[..., Any]


class SubscriptionFeature(GenericCogFeature):
    """A composition of subscriptions kept in sync with an external service."""

    item_key: str = ""
    external: str = ""
    subscribe: Subscribe
    unsubscribe: Subscribe
    followers: Callable[[str], int]
    preview_sender: Any = None

    def __init__(self, key: str) -> None:
        super().__init__(key)

    def asides(self) -> Mapping[str, AsideAction]:
        """The notification message, previewed one written example at a time."""
        if self.preview_sender is None:
            return {}

        async def preview(interaction: Any, responses: Any) -> None:
            await self.preview_sender(interaction, list(responses))

        return {
            "preview": AsideAction(
                preview, defer=True, cooldown=view_constants.ACTION_COOLDOWN_SECONDS
            )
        }

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
        self, lookup: Lookup, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """The external account behind the typed name, and its picture."""
        if self.external not in lookup.services:
            return {}
        if self.external == "twitch":
            info = await asyncio.to_thread(self._twitch_user, lookup.value)
            return {
                "twitch": {
                    self.lookup_result: info.get("id"),
                    "profile_image": str(info.get("profile_image_url") or ""),
                }
            }
        found = await asyncio.to_thread(self._lookup, lookup.value)
        image = (
            await asyncio.to_thread(self._profile_image, lookup.value, found)
            if found
            else ""
        )
        return {self.external: {self.lookup_result: found, "profile_image": image}}

    def _twitch_user(self, name: str) -> Mapping[str, Any]:
        """The Twitch account behind a login, id and picture in one request."""
        try:
            return cast(Any, app_module).bot.twitch.get_user_info(name) or {}
        except Exception:
            return {}

    def _profile_image(self, name: str, found: Any) -> str:
        bot = cast(Any, app_module).bot
        try:
            snippet = bot.youtube.get_channel_info(str(found)) or {}
            default = (snippet.get("thumbnails") or {}).get("default") or {}
            return str(default.get("url") or "")
        except Exception:
            return ""

    lookup_result: str = "user_id"

    def _gated(self) -> bool:
        return bool(cast(Any, app_module).bot.config.is_dev())

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

    async def commit_unpause(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Every saved item subscribes again before the feature is on, outside dev."""
        if not self._gated():
            _saved, items = await self.saved_list(context)
            for item in items:
                await asyncio.to_thread(self.subscribe, None, item)

        return await super().commit_unpause(payload, context)

    async def before_item_added(
        self, item: Mapping[str, Any], context: CommitContext
    ) -> None:
        """A new item subscribes before it is saved, outside dev."""
        if self._gated():
            return
        await asyncio.to_thread(self.subscribe, None, item)

    async def before_item_replaced(
        self, old: Mapping[str, Any], new: Mapping[str, Any], context: CommitContext
    ) -> None:
        """A new name is subscribed before the edit is saved, outside dev."""
        if self._gated() or _name(old, self.item_key) == _name(new, self.item_key):
            return
        await asyncio.to_thread(self.subscribe, None, new)

    async def after_item_replaced(
        self, old: Mapping[str, Any], new: Mapping[str, Any], context: CommitContext
    ) -> None:
        """The old name is let go once the edit is saved, outside dev."""
        if self._gated() or _name(old, self.item_key) == _name(new, self.item_key):
            return
        await self.release(old)

    async def after_item_removed(
        self, item: Mapping[str, Any], context: CommitContext
    ) -> None:
        """A removed item lets its subscription go once the removal is saved."""
        await self.release(item)

    async def after_disable(
        self, document: Mapping[str, Any], context: CommitContext
    ) -> None:
        """Every item of the dropped document lets its subscription go."""
        composition = self.definition.composition
        if composition is None:
            return
        for item in items_of(document, composition.key):
            await self.release(item)

    async def release(self, item: Mapping[str, Any]) -> None:
        """Unsubscribe the item's name once no server follows it anymore."""
        name = unwrap(item.get(self.item_key))
        if not name:
            return

        followers = await asyncio.to_thread(self.followers, str(name))
        if followers == 0:
            await asyncio.to_thread(self.unsubscribe, None, item)


def _name(item: Mapping[str, Any], key: str) -> str:
    entry = item.get(key)
    value = entry.get("value") if isinstance(entry, Mapping) else entry
    return str(value or "").lower()

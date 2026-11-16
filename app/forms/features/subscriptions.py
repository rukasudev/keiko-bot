"""What Twitch and YouTube notifications share: one subscription per item."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import app as app_module
from app.constants import ViewConstants as view_constants
from app.core.commands import command_for
from app.forms.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    GenericCogFeature,
    OpenContext,
)
from app.forms.form.form_state import Answer
from app.forms.form.lookups import Lookup
from app.forms.form.responses.responses import item_values, items_of

Subscribe = Callable[[Any, Mapping[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class Account:
    """The account an external service keeps behind a typed name, and its picture."""

    id: str | None
    picture: str


class SubscriptionFeature(GenericCogFeature):
    """A composition of subscriptions kept in sync with an external service."""

    item_key: str = ""
    external: str = ""
    lookup_result: str = "user_id"
    subscribe: Subscribe
    unsubscribe: Subscribe
    followers: Callable[[str], Awaitable[int]]
    announcers: Callable[[str], Awaitable[int]]
    find_account: Callable[[str], Awaitable[Account]]
    renewal_title: str = ""
    preview_sender: Any = None

    def __init__(self, key: str) -> None:
        super().__init__(key)
        _DECLARED.append(self)

    async def renewals(self) -> Mapping[str, str]:
        """The item of every renewal reminder Keiko keeps, by reminder id."""
        return {}

    async def forget_renewal(self, reminder_id: str) -> None:
        """Delete a renewal reminder on the reminders API, then Keiko's record of it."""

    def asides(self) -> Mapping[str, AsideAction]:
        """The statistics, and the notification previewed one example at a time."""
        if self.preview_sender is None:
            return super().asides()

        async def preview(interaction: Any, responses: Any) -> None:
            await self.preview_sender(interaction, list(responses))

        return {
            **super().asides(),
            "preview": AsideAction(
                preview, defer=True, cooldown=view_constants.ACTION_COOLDOWN_SECONDS
            ),
        }

    async def prefetch(
        self, lookup: Lookup, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """The external account behind the typed name, and its picture."""
        if self.external not in lookup.services:
            return {}
        account = await self.find_account(lookup.value)
        return {
            self.external: {
                self.lookup_result: account.id,
                "profile_image": account.picture,
            }
        }

    def _gated(self) -> bool:
        return app_module.bot.config.is_dev()

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
            await self.subscribe(None, item)
        return document

    async def commit_unpause(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Every saved item subscribes again before the feature is on, outside dev."""
        if not self._gated():
            _saved, items = await self.saved_list(context)
            for item in items:
                await self.subscribe(None, item)

        return await super().commit_unpause(payload, context)

    async def before_item_added(
        self, item: Mapping[str, Any], context: CommitContext
    ) -> None:
        """A new item subscribes before it is saved, outside dev."""
        if self._gated():
            return
        await self.subscribe(None, item)

    async def before_item_replaced(
        self, old: Mapping[str, Any], new: Mapping[str, Any], context: CommitContext
    ) -> None:
        """A new name is subscribed before the edit is saved, outside dev."""
        if self._gated() or _name(old, self.item_key) == _name(new, self.item_key):
            return
        await self.subscribe(None, new)

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
        name = item_values(item).get(self.item_key)
        if not name:
            return

        followers = await self.followers(str(name))
        if followers == 0:
            await self.unsubscribe(None, item)


_DECLARED: list[SubscriptionFeature] = []


def subscription_feature(key: str) -> SubscriptionFeature:
    """The subscription feature the command `key` declares."""
    declared = command_for(key).feature
    found = next((feature for feature in _DECLARED if feature is declared), None)
    if found is None:
        raise KeyError(f"{key!r} keeps no subscriptions")
    return found


def _name(item: Mapping[str, Any], key: str) -> str:
    return str(item_values(item).get(key) or "").lower()

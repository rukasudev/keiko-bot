"""The contract a feature implements, and the generic feature behind a plain form."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.constants import Commands
from app.data import cogs as cogs_data
from app.data import moderations as moderations_data
from app.services.cache import get_cog_data_or_populate, remove_cog_cache_by_guild
from app.services.cogs import LIFECYCLE_ANALYTICS_EVENTS
from app.services.moderations import parse_default_moderations
from app.settings.form.actions.action import PanelRow
from app.settings.form.components import Button
from app.settings.form.form_state import Answer
from app.settings.form.form_yaml import registry
from app.settings.form.lookups import Lookup
from app.settings.form.responses.responses import (
    document_answers,
    items_of,
    to_document,
)
from app.settings.form.responses.summary import item_entries, responses


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


ConfirmValues = Callable[[Mapping[str, Any], str, Any], Mapping[str, str]]


@dataclass(frozen=True)
class AsideAction:
    """A read-only side action of the panel or the review."""

    handler: AsideHandler
    defer: bool = False
    own_response: bool = False
    cooldown: int | None = None
    confirm: str | None = None
    confirm_values: ConfirmValues | None = None


class FeatureModule(Protocol):
    """The contract every feature module fulfils."""

    key: str

    async def open(self, context: OpenContext) -> Opened:
        """The saved document and panel extras, or an empty `Opened` for setup."""

    async def prefetch(
        self, lookup: Lookup, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """The external data the `lookup` of a submitted modal asks for."""

    async def previews_for(
        self, values: Mapping[str, Any], context: OpenContext
    ) -> Mapping[str, str]:
        """The design previews drawn again for the answers on the screen."""

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

    def responses_for_aside(
        self, answers: Mapping[str, Answer], locale: str
    ) -> list[dict[str, Any]]:
        """The answers a side action reads, one item of a list at a time."""


EVENT_KEY = {
    "setup": Commands.ENABLED_KEY,
    "edit": Commands.EDITED_KEY,
    "edit_item": Commands.EDITED_KEY,
    "add_item": Commands.ADDED_KEY,
    "remove_item": Commands.REMOVED_KEY,
    "pause": Commands.PAUSED_KEY,
    "unpause": Commands.UNPAUSED_KEY,
    "disable": Commands.DISABLED_KEY,
}


async def set_moderation(guild_id: str, key: str, value: bool) -> None:
    """Flip the feature flag of a guild, creating the moderations document."""
    existing = await moderations_data.find_moderations_by_guild_async(guild_id)
    if not existing:
        data = parse_default_moderations(guild_id)
        data[key] = value
        await moderations_data.insert_moderations_by_guild_async(data)
        return
    await moderations_data.update_moderations_by_guild_async(guild_id, key, value)


async def record_event(
    key: str, event: str, context: CommitContext, **props: Any
) -> None:
    """The permanent audit record of a state change, and its product event."""
    from app.services import analytics

    data = {
        "guild_id": context.guild_id,
        "cog_key": key,
        "user_id": context.user_id,
        "datetime": context.when,
        "event": event,
    }
    analytics_event = LIFECYCLE_ANALYTICS_EVENTS.get(event)

    if analytics_event:
        analytics.emit(
            analytics_event,
            guild_id=context.guild_id,
            user_id=context.user_id,
            feature=key,
            source=context.source,
            session_id=context.session_id,
            **props,
        )
    await cogs_data.insert_cog_event_async(key, data)


class GenericCogFeature:
    """Persistence every feature shares: the cog document and the audit trail."""

    def __init__(self, key: str) -> None:
        self.key = key

    @property
    def definition(self) -> Any:
        """The compiled definition of this feature's form."""
        return registry.get(self.key)

    async def open(self, context: OpenContext) -> Opened:
        """The saved document, read through the cache like the cogs do."""
        document = await asyncio.to_thread(
            get_cog_data_or_populate, context.guild_id, self.key, True
        )
        if not document:
            return Opened()
        return Opened(
            document=document,
            enabled=bool(document.get(Commands.ENABLED_KEY)),
            info=self.panel_info(context),
            info_title=self.panel_info_title(context),
            extra_buttons=self.extra_buttons(context),
        )

    def panel_info(self, context: OpenContext) -> str:
        """The closing paragraph of the panel, empty by default."""
        return ""

    def panel_info_title(self, context: OpenContext) -> str:
        """The title of the closing paragraph, empty by default."""
        return ""

    def extra_buttons(self, context: OpenContext) -> tuple[Any, ...]:
        """The feature's own panel buttons, none by default."""
        return ()

    async def prefetch(
        self, lookup: Lookup, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """Nothing to look up by default."""
        return {}

    async def previews_for(
        self, values: Mapping[str, Any], context: OpenContext
    ) -> Mapping[str, str]:
        """Nothing to draw again by default."""
        return {}

    def to_document(self, answers: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        """The document the answers persist as."""
        return to_document(self.definition.steps, answers, locale)

    def from_document(self, document: Mapping[str, Any]) -> dict[str, Answer]:
        """The answers a saved document seeds."""
        return document_answers(document)

    def responses_for_preview(
        self, answers: Mapping[str, Answer], locale: str
    ) -> list[dict[str, Any]]:
        """The answers as the legacy preview functions read them.

        A card that is still open holds everything as one draft under its own
        key, so the draft is expanded first; an item of a composition is read
        against the item's own steps.
        """
        views = responses(self.definition.steps, _unpacked(answers), locale)
        composition = self.definition.composition
        if not views and composition is not None:
            views = responses(composition.steps, _unpacked(answers), locale)
        return [{"key": view.key, **view.as_item_entry()} for view in views]

    def responses_for_aside(
        self, answers: Mapping[str, Answer], locale: str
    ) -> list[dict[str, Any]]:
        """The same rows, with a listed composition replaced by its first item.

        A side action runs over the screen the admin is looking at, and from a
        review that screen lists the items rather than holding one of them.
        """
        composition = self.definition.composition
        listed = _first_item(answers, composition.key) if composition else None
        if listed is None:
            return self.responses_for_preview(answers, locale)
        kept = [
            row
            for row in self.responses_for_preview(answers, locale)
            if row.get("key") != composition.key
        ]
        return [*kept, *self.responses_for_preview(_unpacked(listed), locale)]

    def asides(self) -> Mapping[str, AsideAction]:
        """No side actions by default."""
        return {}

    def item_document(self, item: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        """One composition item as the document stores it."""
        composition = self.definition.composition
        return item_entries(composition.steps, item, locale) if composition else {}

    async def commit(
        self, kind: str, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Write what `kind` asks for and record the audit event."""
        handlers = {
            "setup": self.commit_setup,
            "edit": self.commit_edit,
            "edit_item": self.commit_edit_item,
            "add_item": self.commit_add_item,
            "remove_item": self.commit_remove_item,
            "pause": self.commit_pause,
            "unpause": self.commit_unpause,
            "disable": self.commit_disable,
        }
        result = await handlers[kind](payload, context)
        await record_event(
            self.key, EVENT_KEY[kind], context, **self.event_props(kind, payload)
        )

        return result

    def event_props(self, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Extra properties of the audit event, for edits the keys that changed."""
        if kind == "edit":
            keys = sorted(payload.get("answers", {}))
            return {"changed_keys": keys, "changed_count": len(keys)}
        return {}

    async def write_document(self, guild_id: str, document: Mapping[str, Any]) -> None:
        """Replace the guild's document and drop its cache."""
        data = {**document, "guild_id": str(guild_id)}
        await asyncio.to_thread(remove_cog_cache_by_guild, guild_id, self.key)
        await cogs_data.insert_cog_by_guild_id_async(self.key, data)

    async def update_document(self, guild_id: str, data: Mapping[str, Any]) -> None:
        """Merge `data` into the guild's document and drop its cache."""
        payload = {**data, "guild_id": str(guild_id)}
        await asyncio.to_thread(remove_cog_cache_by_guild, guild_id, self.key)
        await cogs_data.update_cog_by_guild_async(guild_id, self.key, payload)

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Enable the feature and store the whole document."""
        document = self.to_document(payload["answers"], context.locale)
        document = await self.before_setup(document, payload["answers"], context)
        await set_moderation(context.guild_id, self.key, True)
        await self.write_document(context.guild_id, document)
        return CommitResult(("moderations", self.key), document=document)

    async def before_setup(
        self,
        document: dict[str, Any],
        answers: Mapping[str, Answer],
        context: CommitContext,
    ) -> dict[str, Any]:
        """A hook for features that add to the document before it is stored."""
        return document

    async def before_edit(
        self,
        changes: dict[str, Any],
        answers: Mapping[str, Answer],
        context: CommitContext,
    ) -> dict[str, Any]:
        """A hook for features that add to an edit before it is merged."""
        return changes

    async def commit_edit(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Merge the edited answers into the document."""
        changes = self.to_document(payload["answers"], context.locale)
        changes.pop("enabled", None)
        changes.pop("schema_version", None)
        changes = await self.before_edit(changes, payload["answers"], context)
        await self.update_document(context.guild_id, changes)
        return CommitResult((self.key,), document={**context.document, **changes})

    def _items_with(
        self, context: CommitContext, item: Mapping[str, Answer], index: int | None
    ) -> list[dict[str, Any]]:
        composition = self.definition.composition
        assert composition is not None
        items = items_of(context.document, composition.key)
        entry = self.item_document(item, context.locale)

        if index is None:
            items.append(entry)
        elif 0 <= index < len(items):
            items[index] = entry
        return items

    async def _store_items(
        self, context: CommitContext, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        composition = self.definition.composition
        assert composition is not None
        stored = {"style": "composition", "values": items}
        await self.update_document(context.guild_id, {composition.key: stored})
        return {**context.document, composition.key: stored}

    async def commit_add_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Append the item and store the list."""
        await self.on_item_added(payload["answers"], context)
        document = await self._store_items(
            context, self._items_with(context, payload["answers"], None)
        )
        return CommitResult((self.key,), document=document)

    async def commit_edit_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Replace the item at its index and store the list."""
        index = payload.get("index")
        await self.on_item_edited(payload["answers"], index, context)
        document = await self._store_items(
            context, self._items_with(context, payload["answers"], index)
        )
        return CommitResult((self.key,), document=document)

    async def commit_remove_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Drop the item at its index and store the list."""
        composition = self.definition.composition
        assert composition is not None
        items = items_of(context.document, composition.key)
        index = int(payload.get("index", -1))
        removed = items.pop(index) if 0 <= index < len(items) else {}
        await self.on_item_removed(removed, context)
        document = await self._store_items(context, items)

        return CommitResult((self.key,), document=document)

    async def commit_pause(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Flag the feature off and mark the document paused."""
        await set_moderation(context.guild_id, self.key, False)
        await self.update_document(context.guild_id, {Commands.ENABLED_KEY: False})
        return CommitResult(("moderations", self.key))

    async def commit_unpause(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Flag the feature on and mark the document enabled."""
        await set_moderation(context.guild_id, self.key, True)
        await self.update_document(context.guild_id, {Commands.ENABLED_KEY: True})
        return CommitResult(("moderations", self.key))

    async def commit_disable(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Undo the feature's side effects, flag it on again, drop the document."""
        await self.on_disable(context)
        await set_moderation(context.guild_id, self.key, True)
        await self.update_document(context.guild_id, {Commands.ENABLED_KEY: True})
        await asyncio.to_thread(remove_cog_cache_by_guild, context.guild_id, self.key)
        await cogs_data.delete_cog_by_guild_id_async(context.guild_id, self.key)
        return CommitResult(("moderations", self.key))

    async def on_disable(self, context: CommitContext) -> None:
        """Nothing to undo by default."""

    async def on_item_added(
        self, item: Mapping[str, Answer], context: CommitContext
    ) -> None:
        """Nothing else happens when an item is added by default."""

    async def on_item_edited(
        self, item: Mapping[str, Answer], index: int | None, context: CommitContext
    ) -> None:
        """Nothing else happens when an item is edited by default."""

    async def on_item_removed(
        self, item: Mapping[str, Any], context: CommitContext
    ) -> None:
        """Nothing else happens when an item is removed by default."""


def _unpacked(answers: Mapping[str, Answer]) -> dict[str, Answer]:
    found: dict[str, Answer] = {}
    for key, answer in answers.items():
        if answer.parts:
            found.update({part: Answer(value) for part, value in answer.parts.items()})
            continue
        found[key] = answer
    return found


def _first_item(answers: Mapping[str, Answer], key: str) -> dict[str, Answer] | None:
    """The answers of the first item, when `answers` holds the whole list."""
    answer = answers.get(key)
    items = list(answer.raw or ()) if answer else []
    if not items or not isinstance(items[0], Mapping):
        return None
    return {
        name: value if isinstance(value, Answer) else Answer(value)
        for name, value in items[0].items()
    }

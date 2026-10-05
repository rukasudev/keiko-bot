"""The contract a feature implements, and the generic feature behind a plain form."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app import logger
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.data import cogs as cogs_data
from app.exceptions import ErrorContext
from app.services.cache import get_cog_data_or_populate, remove_cog_cache_by_guild
from app.services.cogs import LIFECYCLE_ANALYTICS_EVENTS, is_feature_on
from app.services.moderations import set_feature_enabled
from app.settings.form.form_state import Answer
from app.settings.form.form_yaml import CompositionStep, FormDefinition, registry
from app.settings.form.lookups import Lookup
from app.settings.form.manager import PanelExtras
from app.settings.form.responses.responses import (
    document_answers,
    item_values,
    items_of,
    to_document,
)
from app.settings.form.responses.summary import (
    item_entries,
    listed,
    responses,
    unwrap,
)


@dataclass(frozen=True)
class OpenContext:
    """Who opens the feature, where, the guild objects the bot holds and its prefix."""

    guild_id: str
    user_id: str
    locale: str
    guild: Any = None
    member: Any = None
    prefix: str = ""


@dataclass(frozen=True)
class Opened:
    """What the feature found when opened: a document to manage, or nothing."""

    document: Mapping[str, Any] | None = None
    panel: PanelExtras = field(default_factory=PanelExtras)
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
    "toggle": Commands.EDITED_KEY,
    "edit_item": Commands.EDITED_KEY,
    "add_item": Commands.ADDED_KEY,
    "remove_item": Commands.REMOVED_KEY,
    "pause": Commands.PAUSED_KEY,
    "unpause": Commands.UNPAUSED_KEY,
    "disable": Commands.DISABLED_KEY,
}


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
    def definition(self) -> FormDefinition:
        """The compiled definition of this feature's form."""
        return registry.get(self.key)

    async def open(self, context: OpenContext) -> Opened:
        """The saved document, read through the cache like the cogs do."""
        document = await asyncio.to_thread(
            get_cog_data_or_populate, context.guild_id, self.key, True
        )
        if not document:
            return Opened()
        enabled = await asyncio.to_thread(
            is_feature_on, context.guild_id, self.key, document
        )
        return Opened(
            document=document,
            panel=PanelExtras(
                info=self.panel_info(context),
                info_title=self.panel_info_title(context),
                extra_buttons=self.extra_buttons(context),
                enabled=enabled,
            ),
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
        return document_answers(self.normalized(document))

    def normalized(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """The saved document as the feature reads it; as stored by default."""
        return dict(document)

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
        first = _first_item(answers, composition.key) if composition else None
        if composition is None or first is None:
            return self.responses_for_preview(answers, locale)

        kept = [
            row
            for row in self.responses_for_preview(answers, locale)
            if row.get("key") != composition.key
        ]
        return [*kept, *self.responses_for_preview(_unpacked(first), locale)]

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
            "toggle": self.commit_toggle,
            "edit_item": self.commit_edit_item,
            "add_item": self.commit_add_item,
            "remove_item": self.commit_remove_item,
            "pause": self.commit_pause,
            "unpause": self.commit_unpause,
            "disable": self.commit_disable,
        }
        try:
            result = await handlers[kind](payload, context)
        except (ListChanged, DuplicateItem):
            await asyncio.to_thread(
                remove_cog_cache_by_guild, context.guild_id, self.key
            )
            raise

        await record_event(
            self.key, EVENT_KEY[kind], context, **self.event_props(kind, payload)
        )
        return result

    def event_props(self, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Extra properties of the audit event, for edits the keys that changed."""
        if kind == "toggle":
            keys = [str(payload["key"])]
        elif kind == "edit":
            keys = sorted(payload.get("answers", {}))
        else:
            return {}
        return {"changed_keys": keys, "changed_count": len(keys)}

    async def write_document(self, guild_id: str, document: Mapping[str, Any]) -> None:
        """Replace the guild's document, then drop its cache."""
        data = {**document, "guild_id": str(guild_id)}
        await cogs_data.insert_cog_by_guild_id_async(self.key, data)
        await asyncio.to_thread(remove_cog_cache_by_guild, guild_id, self.key)

    async def update_document(self, guild_id: str, data: Mapping[str, Any]) -> None:
        """Merge `data` into the guild's document, then drop its cache."""
        payload = {**data, "guild_id": str(guild_id)}
        await cogs_data.update_cog_by_guild_async(guild_id, self.key, payload)
        await asyncio.to_thread(remove_cog_cache_by_guild, guild_id, self.key)

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Store the whole document and record the feature as on."""
        document = self.to_document(payload["answers"], context.locale)
        document = await self.before_setup(document, payload["answers"], context)
        await self.write_document(context.guild_id, document)
        await asyncio.to_thread(set_feature_enabled, context.guild_id, self.key, True)
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
        """Merge the edited answers into the document, while it is still saved."""
        await self.saved_document(context)
        changes = self.to_document(payload["answers"], context.locale)
        changes.pop("enabled", None)
        changes.pop("schema_version", None)
        changes = await self.before_edit(changes, payload["answers"], context)
        await self.update_document(context.guild_id, changes)
        return CommitResult((self.key,), document={**context.document, **changes})

    async def commit_toggle(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Turn one option on or off as the screen asked, in the choice saved now."""
        key, option = str(payload["key"]), str(payload["value"])
        document = await self.saved_document(context)
        chosen = [
            str(value) for value in listed(unwrap(self.normalized(document).get(key)))
        ]
        if not payload["turned_on"]:
            toggled = [value for value in chosen if value != option]
        else:
            toggled = chosen if option in chosen else [*chosen, option]

        stored = self.to_document({key: Answer(toggled)}, context.locale)[key]
        written = await self.save_value(context, key, document.get(key), stored)
        return CommitResult((self.key,), document=written)

    @property
    def composition(self) -> CompositionStep:
        """The list step of this feature's form."""
        composition = self.definition.composition
        assert composition is not None
        return composition

    async def commit_add_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Append the item to the list as it is saved now."""
        added = self.item_document(payload["answers"], context.locale)
        saved, items = await self.saved_list(context)
        if len(items) >= self.composition.items.max:
            raise ListChanged(self.key)
        self.refuse_duplicate(items, added)

        await self.before_item_added(added, context)
        document = await self.save_or_let_go(context, saved, [*items, added], added)
        return CommitResult((self.key,), document=document)

    async def commit_edit_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Replace the edited item, while it is saved as the admin's panel showed it."""
        edited = self.shown_item(context, payload.get("index"))
        replacement = self.item_document(payload["answers"], context.locale)
        saved, items = await self.saved_list(context)
        position = _position(items, edited)
        self.refuse_duplicate([*items[:position], *items[position + 1 :]], replacement)
        items[position] = replacement

        await self.before_item_replaced(edited, replacement, context)
        document = await self.save_or_let_go(context, saved, items, replacement)
        await self.best_effort(
            self.after_item_replaced(edited, replacement, context), context
        )
        return CommitResult((self.key,), document=document)

    async def commit_remove_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Drop the item the admin chose, while it is saved as their panel showed it."""
        removed = self.shown_item(context, payload.get("index"))
        saved, items = await self.saved_list(context)
        del items[_position(items, removed)]

        document = await self.save_items(context, saved, items)
        await self.best_effort(self.after_item_removed(removed, context), context)
        return CommitResult((self.key,), document=document)

    def shown_item(self, context: CommitContext, index: Any) -> dict[str, Any]:
        """The item at `index` of the list the admin's panel showed."""
        shown = items_of(context.document, self.composition.key)
        position = -1 if index is None else int(index)
        if not 0 <= position < len(shown):
            raise ListChanged(self.key)
        return shown[position]

    async def saved_document(self, context: CommitContext) -> dict[str, Any]:
        """The document as it is saved now; a feature disabled meanwhile has none."""
        document = await cogs_data.find_cog_by_guild_id_async(
            context.guild_id, self.key
        )
        if document is None:
            raise ListChanged(self.key)
        return dict(document)

    async def saved_list(
        self, context: CommitContext
    ) -> tuple[Any, list[dict[str, Any]]]:
        """The list as it is saved now: its stored value and its items."""
        key = self.composition.key
        document = await self.saved_document(context)
        return document.get(key), items_of(self.normalized(document), key)

    def refuse_duplicate(
        self, items: Sequence[Mapping[str, Any]], item: Mapping[str, Any]
    ) -> None:
        """Refuse an item whose unique value one of `items` already holds."""
        unique = self.composition.items.unique_by
        if not unique:
            return

        wanted = str(item_values(item).get(unique))
        if any(str(item_values(other).get(unique)) == wanted for other in items):
            raise DuplicateItem(wanted)

    async def save_or_let_go(
        self,
        context: CommitContext,
        saved: Any,
        items: list[dict[str, Any]],
        taken: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Store the list; when it cannot be, let go of what `taken` took outside."""
        try:
            return await self.save_items(context, saved, items)
        except Exception:
            await self.best_effort(self.after_item_removed(taken, context), context)
            raise

    async def save_items(
        self, context: CommitContext, saved: Any, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Store the list if it is still saved as it was read."""
        stored = {"style": "composition", "values": items}
        return await self.save_value(context, self.composition.key, saved, stored)

    async def save_value(
        self, context: CommitContext, key: str, saved: Any, value: Any
    ) -> dict[str, Any]:
        """Store `value` under `key` while it is saved as read, then drop the cache."""
        written = await cogs_data.update_cog_if_unchanged_async(
            context.guild_id, self.key, key, saved, value
        )
        if not written:
            raise ListChanged(self.key)

        await asyncio.to_thread(remove_cog_cache_by_guild, context.guild_id, self.key)
        return {**context.document, key: value}

    async def best_effort(
        self, effect: Awaitable[None], context: CommitContext
    ) -> None:
        """Run an effect outside the document, logging a failure instead of raising."""
        try:
            await effect
        except Exception as error:
            logger.error(
                f"{self.key}: an effect outside the document failed: {error!r}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(
                    flow=f"form_{self.key}",
                    guild_id=context.guild_id,
                    user_id=context.user_id,
                    extra={"session_id": context.session_id},
                ),
                exc_info=True,
            )

    async def commit_pause(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Record the feature as off, keeping its document, while it is still saved."""
        await self.saved_document(context)
        await asyncio.to_thread(set_feature_enabled, context.guild_id, self.key, False)
        return CommitResult(("moderations", self.key))

    async def commit_unpause(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Record the feature as on again, while its document is still saved."""
        await self.saved_document(context)
        await asyncio.to_thread(set_feature_enabled, context.guild_id, self.key, True)
        return CommitResult(("moderations", self.key))

    async def commit_disable(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Drop the document, undo what it set up, then record the feature as off."""
        dropped = await cogs_data.delete_cog_by_guild_id_async(
            context.guild_id, self.key
        )
        await asyncio.to_thread(remove_cog_cache_by_guild, context.guild_id, self.key)
        await self.best_effort(self.after_disable(dropped or {}, context), context)

        await asyncio.to_thread(set_feature_enabled, context.guild_id, self.key, False)
        return CommitResult(("moderations", self.key))

    async def before_item_added(
        self, item: Mapping[str, Any], context: CommitContext
    ) -> None:
        """What a new item needs outside before it is saved; nothing by default."""

    async def before_item_replaced(
        self, old: Mapping[str, Any], new: Mapping[str, Any], context: CommitContext
    ) -> None:
        """What an edited item needs outside before it is saved; nothing by default."""

    async def after_item_replaced(
        self, old: Mapping[str, Any], new: Mapping[str, Any], context: CommitContext
    ) -> None:
        """What the version an edit replaced leaves outside; nothing by default."""

    async def after_item_removed(
        self, item: Mapping[str, Any], context: CommitContext
    ) -> None:
        """What a removed item leaves outside; nothing by default."""

    async def after_disable(
        self, document: Mapping[str, Any], context: CommitContext
    ) -> None:
        """What the dropped document leaves outside; nothing by default."""


class DuplicateItem(Exception):
    """The item to add is already on the list."""


class ListChanged(Exception):
    """The settings are no longer saved as the admin's panel showed them."""


def _position(items: Sequence[Mapping[str, Any]], item: Mapping[str, Any]) -> int:
    for position, saved in enumerate(items):
        if saved == item:
            return position
    raise ListChanged(str(item))


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

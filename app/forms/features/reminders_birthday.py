"""Birthday reminders: its own collections, its own rows, its own lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.data import birthdays as birthdays_data
from app.forms.engine.screen import Button
from app.forms.extensions.copy import text
from app.forms.features.generic import GenericCogFeature, set_moderation
from app.forms.features.protocol import (
    AsideAction,
    CommitContext,
    CommitResult,
    OpenContext,
    Opened,
)
from app.forms.kinds.context import PanelRow
from app.services.reminders_birthdays import (
    birthday_manager_cog_data,
    birthday_settings_rows,
    disable_birthdays,
    remove_birthday,
    save_birthday_config_changes,
    save_form_birthday_item,
    save_setup_form,
    send_stats_message,
)


def _copy(key: str, locale: str) -> str:
    return text(f"commands.commands.commons.reminders-birthdays-manager.{key}", locale)


class BirthdayFeature(GenericCogFeature):
    """Birthdays live in `reminders.birthdays`; the config in its own document."""

    def __init__(self) -> None:
        super().__init__(Commands.REMINDERS_BIRTHDAY_KEY)

    async def open(self, context: OpenContext) -> Opened:
        """The synthetic document the panel reads, or nothing when unconfigured."""
        guild_id = context.guild_id
        config = await asyncio.to_thread(birthdays_data.find_birthday_config, guild_id)
        enabled = await asyncio.to_thread(birthdays_data.is_birthday_enabled, guild_id)
        if not enabled or not config:
            return Opened()
        document = await asyncio.to_thread(birthday_manager_cog_data, guild_id)
        rows = await asyncio.to_thread(birthday_settings_rows, guild_id, context.locale)
        return Opened(
            document=document,
            rows=tuple(
                PanelRow(
                    key="",
                    title=row["title"],
                    value=row.get("value"),
                    style=row.get("style"),
                )
                for row in rows
            ),
            enabled=bool(document.get(Commands.ENABLED_KEY)),
            extra_buttons=self.extra_buttons(context),
        )

    def extra_buttons(self, context: OpenContext) -> tuple[Button, ...]:
        """The stats button."""
        locale = context.locale
        return (
            Button(
                _copy("stats.button.label", locale),
                "aside:stats",
                "secondary",
                "📊",
                description=_copy("stats.button.desc", locale),
            ),
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """The stats screen."""

        async def stats(interaction: Any, _responses: Any) -> None:
            await send_stats_message(interaction)

        return {
            "stats": AsideAction(
                stats, defer=True, cooldown=view_constants.ACTION_COOLDOWN_SECONDS
            )
        }

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """The config document, the first birthday and its reminder."""
        responses = self.responses_for_preview(payload["answers"], context.locale)
        await asyncio.to_thread(
            save_setup_form, context.guild_id, responses, context.locale
        )
        return CommitResult(("reminders_birthday", "birthdays"))

    async def commit_edit(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Edited settings land on the config document."""
        changes = self.to_document(payload["answers"], context.locale)
        await asyncio.to_thread(
            save_birthday_config_changes, context.guild_id, changes, context.locale
        )
        return CommitResult(("reminders_birthday",))

    async def commit_edit_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """An edited member keeps its own birthday document."""
        item = self.item_document(payload["answers"], context.locale)
        await asyncio.to_thread(save_form_birthday_item, context.guild_id, item)
        return CommitResult(("birthdays",))

    async def commit_add_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """A new member's birthday, refused when the member is already listed."""
        item = self.item_document(payload["answers"], context.locale)
        user_id = str(_value(item.get("user")) or "")
        existing = await asyncio.to_thread(
            birthdays_data.find_birthday_item, context.guild_id, user_id
        )
        if existing:
            raise DuplicateItem(user_id)
        await asyncio.to_thread(save_form_birthday_item, context.guild_id, item)
        return CommitResult(("birthdays",))

    async def commit_remove_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """The member's birthday and, when unused, its reminder go."""
        item = payload.get("item") or {}
        user_id = _value(item.get("user"))
        if user_id:
            await asyncio.to_thread(remove_birthday, context.guild_id, str(user_id))
        return CommitResult(("birthdays",))

    async def commit_disable(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Every birthday, reminder and the config go; the flag comes back on."""
        await asyncio.to_thread(disable_birthdays, context.guild_id)
        await set_moderation(context.guild_id, self.key, True)
        await self.update_document(context.guild_id, {Commands.ENABLED_KEY: True})
        return CommitResult(("reminders_birthday", "birthdays", "moderations"))


class DuplicateItem(Exception):
    """The item to add is already on the list."""


def _value(entry: Any) -> Any:
    if isinstance(entry, Mapping):
        return entry.get("value") or entry.get("values")
    return entry


FEATURE = BirthdayFeature()

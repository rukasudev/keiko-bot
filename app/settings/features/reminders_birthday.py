"""Birthday reminders: its own collections, its own rows, its own lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.data import birthdays as birthdays_data
from app.services.cache import remove_cog_cache_by_guild
from app.services.cogs import is_feature_on
from app.services.dates import utc_today
from app.services.moderations import set_feature_enabled, update_moderations_by_guild
from app.services.reminders_birthdays import (
    birthday_found_members,
    birthday_manager_cog_data,
    birthday_month_pages,
    birthday_settings_rows,
    birthday_stats_lines,
    disable_birthdays,
    remove_birthday,
    save_birthday_config_changes,
    save_form_birthday_item,
    save_setup_form,
    send_birthday_preview,
)
from app.settings.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    DuplicateItem,
    GenericCogFeature,
    ListChanged,
    OpenContext,
    Opened,
)
from app.settings.form.manager import FoundItem, PanelExtras, PanelPage
from app.settings.form.responses.summary import PanelRow


class BirthdayFeature(GenericCogFeature):
    """Birthdays live in `reminders.birthdays`; the config in its own document."""

    def __init__(self, today: Callable[[], date] = utc_today) -> None:
        super().__init__(Commands.REMINDERS_BIRTHDAY_KEY)
        self.today = today

    async def open(self, context: OpenContext) -> Opened:
        """The synthetic document the panel reads, paused or not, or nothing saved."""
        guild_id = context.guild_id
        config = await asyncio.to_thread(birthdays_data.find_birthday_config, guild_id)
        if not config:
            return Opened()

        enabled = await asyncio.to_thread(is_feature_on, guild_id, self.key, config)
        document = await asyncio.to_thread(birthday_manager_cog_data, guild_id)
        today = self.today()
        rows = await asyncio.to_thread(
            birthday_settings_rows, guild_id, context.locale, today
        )
        pages = await asyncio.to_thread(
            birthday_month_pages, guild_id, context.locale, today
        )
        found = await asyncio.to_thread(
            birthday_found_members, guild_id, context.locale
        )
        return Opened(
            document=document,
            panel=PanelExtras(
                rows=tuple(
                    PanelRow(
                        key=row.get("key", ""),
                        title=row["title"],
                        value=row.get("value"),
                        style=row.get("style"),
                    )
                    for row in rows
                ),
                enabled=enabled,
                pages=tuple(
                    PanelPage(title, tuple(lines), description)
                    for title, description, lines in pages
                ),
                found={
                    user_id: FoundItem(tuple(lines)) for user_id, lines in found.items()
                },
            ),
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """The statistics and the preview of the celebration."""

        async def preview(interaction: Any, responses: Any) -> None:
            await send_birthday_preview(interaction, list(responses))

        return {
            **super().asides(),
            "preview": AsideAction(
                preview, defer=True, cooldown=view_constants.ACTION_COOLDOWN_SECONDS
            ),
        }

    def stats_lines(self, guild_id: str, locale: str) -> Sequence[str]:
        """How many birthdays, this month's, and the busiest and quietest dates."""
        return birthday_stats_lines(guild_id, locale, self.today())

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """The config, the first birthday and its reminder; the feature is on."""
        responses = self.responses_for_preview(payload["answers"], context.locale)
        await asyncio.to_thread(
            save_setup_form, context.guild_id, responses, context.locale
        )
        await asyncio.to_thread(set_feature_enabled, context.guild_id, self.key, True)
        return CommitResult(("reminders_birthday", "birthdays", "moderations"))

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
        """Every chosen member's birthday goes while all are saved, or none does."""
        user_ids = [str(_value(item.get("user")) or "") for item in payload["items"]]
        for user_id in user_ids:
            saved = await asyncio.to_thread(
                birthdays_data.find_birthday_item, context.guild_id, user_id
            )
            if not user_id or saved is None:
                raise ListChanged(self.key)

        for user_id in user_ids:
            await asyncio.to_thread(remove_birthday, context.guild_id, user_id)
        return CommitResult(("birthdays",))

    async def commit_disable(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Every birthday, reminder and the config go; the feature is off."""
        await asyncio.to_thread(disable_birthdays, context.guild_id)
        await asyncio.to_thread(remove_cog_cache_by_guild, context.guild_id, self.key)
        await asyncio.to_thread(
            update_moderations_by_guild, context.guild_id, self.key, False
        )
        return CommitResult(("reminders_birthday", "birthdays", "moderations"))


def _value(entry: Any) -> Any:
    if isinstance(entry, Mapping):
        return entry.get("value") or entry.get("values")
    return entry


FEATURE = BirthdayFeature()

"""Block links: legacy documents upgraded at read time, records on the panel."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.data.block_links import delete_blocked_links_by_guild
from app.services.block_links import (
    normalize_block_links_config,
    send_blocked_links_message,
    send_blocked_links_stats_message,
)
from app.settings.features.feature import (
    AsideAction,
    CommitContext,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.settings.form.components import Button
from app.settings.form.copy import text


def _copy(key: str, locale: str) -> str:
    return text(f"commands.commands.commons.block-links-manager.{key}", locale)


class BlockLinksFeature(GenericCogFeature):
    """The link blocker: its document predates the card, so reads normalize."""

    def __init__(self) -> None:
        super().__init__(Commands.BLOCK_LINKS_KEY)

    async def open(self, context: OpenContext) -> Opened:
        """The document as the card understands it, plus the records buttons."""
        opened = await super().open(context)
        if opened.document is None:
            return opened
        return Opened(
            document=self.normalized(opened.document),
            enabled=opened.enabled,
            info=_copy("info", context.locale),
            info_title=_copy("info-title", context.locale),
            extra_buttons=self.extra_buttons(context),
        )

    def extra_buttons(self, context: OpenContext) -> tuple[Button, ...]:
        """The blocked-links list and the stats."""
        locale = context.locale
        return (
            Button(
                _copy("blocked-list.button.label", locale),
                "aside:blocked_list",
                "secondary",
                "🔎",
                description=_copy("blocked-list.button.desc", locale),
            ),
            Button(
                text("buttons.stats.label", locale),
                "aside:stats",
                "secondary",
                "📊",
                description=_copy("stats.button.desc", locale),
            ),
        )

    def normalized(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """Legacy labels and missing envelopes, upgraded as the bot reads them."""
        return normalize_block_links_config(dict(document))

    def asides(self) -> Mapping[str, AsideAction]:
        """The records browser answers on its own; the stats defer first."""

        async def blocked_list(interaction: Any, _responses: Any) -> None:
            await send_blocked_links_message(interaction)

        async def stats(interaction: Any, _responses: Any) -> None:
            await send_blocked_links_stats_message(interaction)

        return {
            "blocked_list": AsideAction(
                blocked_list,
                own_response=True,
                cooldown=view_constants.ACTION_COOLDOWN_SECONDS,
            ),
            "stats": AsideAction(
                stats, defer=True, cooldown=view_constants.ACTION_COOLDOWN_SECONDS
            ),
        }

    async def after_disable(
        self, document: Mapping[str, Any], context: CommitContext
    ) -> None:
        """The blocked-links records go with the feature."""
        await asyncio.to_thread(delete_blocked_links_by_guild, context.guild_id)


FEATURE = BlockLinksFeature()

"""Block links: legacy documents upgraded at read time, records on the panel."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.data.events import activity
from app.services.block_links import (
    blocked_links_stats_lines,
    normalize_block_links_config,
    send_blocked_links_message,
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
from app.settings.form.manager import PanelExtras


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
            panel=PanelExtras(
                info=_copy("info", context.locale),
                info_title=_copy("info-title", context.locale),
                extra_buttons=self.extra_buttons(context),
                enabled=opened.panel.enabled,
            ),
        )

    def extra_buttons(self, context: OpenContext) -> tuple[Button, ...]:
        """The blocked-links list."""
        locale = context.locale
        return (
            Button(
                _copy("blocked-list.button.label", locale),
                "aside:blocked_list",
                "secondary",
                "🔎",
                description=_copy("blocked-list.button.desc", locale),
            ),
        )

    def normalized(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """Legacy labels and missing envelopes, upgraded as the bot reads them."""
        return normalize_block_links_config(dict(document))

    def asides(self) -> Mapping[str, AsideAction]:
        """The records browser, which answers on its own, beside the statistics."""

        async def blocked_list(interaction: Any, _responses: Any) -> None:
            await send_blocked_links_message(interaction)

        return {
            **super().asides(),
            "blocked_list": AsideAction(
                blocked_list,
                own_response=True,
                cooldown=view_constants.ACTION_COOLDOWN_SECONDS,
            ),
        }

    def stats_lines(self, guild_id: str, locale: str) -> Sequence[str]:
        """The blocks of all time and of the last days, the sites and the member."""
        return blocked_links_stats_lines(guild_id, locale)

    async def after_disable(
        self, document: Mapping[str, Any], context: CommitContext
    ) -> None:
        """The blocked links it recorded go with the feature."""
        await asyncio.to_thread(
            activity.delete_by_guild, context.guild_id, Commands.BLOCK_LINKS_KEY
        )


FEATURE = BlockLinksFeature()

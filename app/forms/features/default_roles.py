"""Default roles: the panel warns about roles the bot cannot assign anymore."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import Commands
from app.forms.engine.screen import Button
from app.forms.extensions.copy import text
from app.forms.features.generic import GenericCogFeature
from app.forms.features.protocol import AsideAction, OpenContext, Opened
from app.services.default_roles import (
    get_not_available_roles,
    set_on_default_roles_sync,
)
from app.services.utils import get_available_roles_by_guild

SYNC_COOLDOWN_SECONDS = 60


class DefaultRolesFeature(GenericCogFeature):
    """Roles for new members and bots, with a sync button on the panel."""

    def __init__(self) -> None:
        super().__init__(Commands.DEFAULT_ROLES_KEY)

    async def open(self, context: OpenContext) -> Opened:
        """The document, or a refusal when the bot can assign no role at all."""
        opened = await super().open(context)
        available = get_available_roles_by_guild(context.guild) if context.guild else {}
        if opened.document is None:
            if not available:
                return Opened(refusal="command-default-roles-low-permissions")
            return opened
        roles = _values(opened.document.get(Commands.DEFAULT_ROLES_KEY))
        return Opened(
            document=opened.document,
            enabled=opened.enabled,
            info=get_not_available_roles(roles, available, context.locale),
            extra_buttons=self.extra_buttons(context),
        )

    def extra_buttons(self, context: OpenContext) -> tuple[Button, ...]:
        """The sync button."""
        locale = context.locale
        return (
            Button(
                text("buttons.roles-sync.label", locale),
                "aside:sync",
                "secondary",
                "🔄",
                description=text("buttons.roles-sync.desc", locale),
            ),
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """Syncing writes roles to every member: one run per minute is plenty."""

        async def sync(interaction: Any, _responses: Any) -> None:
            await set_on_default_roles_sync(interaction)

        return {"sync": AsideAction(sync, defer=True, cooldown=SYNC_COOLDOWN_SECONDS)}


def _values(entry: Any) -> Any:
    if isinstance(entry, Mapping):
        return entry.get("values")
    return entry


FEATURE = DefaultRolesFeature()

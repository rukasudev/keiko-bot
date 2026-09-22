"""Default roles: the panel warns about roles the bot cannot assign anymore."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import Commands
from app.services.default_roles import (
    count_roles_receivers,
    get_not_available_roles,
    set_on_default_roles_sync,
)
from app.services.utils import get_available_roles_by_guild
from app.settings.features.feature import (
    AsideAction,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.settings.form.components import Button
from app.settings.form.copy import text

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

        return {
            "sync": AsideAction(
                sync,
                defer=True,
                cooldown=SYNC_COOLDOWN_SECONDS,
                confirm="buttons.roles-sync.confirm",
                confirm_values=_roles_of,
            )
        }


def _values(entry: Any) -> Any:
    if isinstance(entry, Mapping):
        return entry.get("values")
    return entry


def _roles_of(
    document: Mapping[str, Any], locale: str, guild: Any = None
) -> Mapping[str, str]:
    members, bots = _receivers(document, guild)
    return {
        "members": _mentions(document.get(Commands.DEFAULT_ROLES_KEY), locale),
        "bots": _mentions(document.get(Commands.DEFAULT_ROLES_BOT_KEY), locale),
        "members_count": str(members),
        "bots_count": str(bots),
    }


def _receivers(document: Mapping[str, Any], guild: Any) -> tuple[int, int]:
    """How many members and bots of the guild the sync would hand a role to."""
    members, bots = count_roles_receivers(document, guild)
    return int(members), int(bots)


def _mentions(entry: Any, locale: str) -> str:
    values = _values(entry)
    if isinstance(values, (list, tuple)):
        roles = [str(value) for value in values]
    else:
        roles = [str(values)] if values else []
    joined = ", ".join(f"<@&{role}>" for role in roles)
    return joined or text("commands.resume.empty", locale)


FEATURE = DefaultRolesFeature()

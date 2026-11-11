"""Default roles: the panel warns about roles the bot cannot assign anymore."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.constants import Commands
from app.services.default_roles import (
    count_label,
    count_roles_receivers,
    get_not_available_roles,
    server_totals,
    set_default_roles,
    set_on_default_roles_sync,
)
from app.services.members import every_member
from app.services.utils import get_available_roles_by_guild
from app.settings.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.settings.form.components import Button
from app.settings.form.copy import text
from app.settings.form.form_state import Answer
from app.settings.form.manager import PanelExtras
from app.settings.form.responses.styles import empty_value

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
            return replace(
                opened, values=await _server_totals(context.guild, context.locale)
            )
        roles = _values(opened.document.get(Commands.DEFAULT_ROLES_KEY))
        return Opened(
            document=opened.document,
            panel=PanelExtras(
                info=get_not_available_roles(roles, available, context.locale),
                extra_buttons=self.extra_buttons(context),
                enabled=opened.panel.enabled,
            ),
        )

    def to_document(self, answers: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        """The roles to save; whether to give them out now is never stored."""
        document = super().to_document(answers, locale)
        document.pop("sync_existing", None)
        return document

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Save the roles, then give them to who is already there when asked."""
        result = await super().commit_setup(payload, context)
        wanted = payload["answers"].get("sync_existing")

        if wanted is not None and wanted.raw and context.guild is not None:
            guild = context.guild
            self.in_background(
                set_default_roles(
                    dict(result.document or {}), guild, every_member(guild)
                ),
                context,
            )
        return result

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
        """The statistics, and the sync, run once a minute at most."""

        async def sync(interaction: Any, _responses: Any) -> None:
            await set_on_default_roles_sync(interaction)

        return {
            **super().asides(),
            "sync": AsideAction(
                sync,
                defer=True,
                cooldown=SYNC_COOLDOWN_SECONDS,
                confirm="buttons.roles-sync.confirm",
                confirm_values=_roles_of,
            ),
        }


async def _server_totals(guild: Any, locale: str) -> dict[str, str]:
    members, bots = await server_totals(guild)
    return {
        "members_count": count_label(members, locale),
        "bots_count": count_label(bots, locale),
    }


def _values(entry: Any) -> Any:
    if isinstance(entry, Mapping):
        return entry.get("values")
    return entry


async def _roles_of(
    document: Mapping[str, Any], locale: str, guild: Any = None
) -> Mapping[str, str]:
    members, bots = await count_roles_receivers(document, guild)
    return {
        "members": _mentions(document.get(Commands.DEFAULT_ROLES_KEY), locale),
        "bots": _mentions(document.get(Commands.DEFAULT_ROLES_BOT_KEY), locale),
        "members_count": count_label(members, locale),
        "bots_count": count_label(bots, locale),
    }


def _mentions(entry: Any, locale: str) -> str:
    values = _values(entry)
    if isinstance(values, (list, tuple)):
        roles = [str(value) for value in values]
    else:
        roles = [str(values)] if values else []
    joined = ", ".join(f"<@&{role}>" for role in roles)
    return joined or empty_value(locale)


FEATURE = DefaultRolesFeature()

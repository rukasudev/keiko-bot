"""Welcome messages: design previews before the gallery, a preview button after."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.services.welcome_messages import (
    generate_design_previews,
    send_welcome_message_preview,
)
from app.settings.features.feature import (
    AsideAction,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.settings.form.components import Button
from app.settings.form.copy import text


class WelcomeMessagesFeature(GenericCogFeature):
    """A personalized banner and message for every new member."""

    def __init__(self) -> None:
        super().__init__(Commands.WELCOME_MESSAGES_KEY)

    async def open(self, context: OpenContext) -> Opened:
        """The document with the preview button, or the previews for a setup."""
        opened = await super().open(context)
        if opened.document is not None:
            return Opened(
                document=opened.document,
                enabled=opened.enabled,
                extra_buttons=self.extra_buttons(context),
                pending_previews=self.previews(context),
            )
        return Opened(pending_previews=self.previews(context))

    async def previews(self, context: OpenContext) -> Mapping[str, str]:
        """One rendered banner per design, drawn in the background while the
        member answers the first steps."""
        designs = [{"key": design.key} for design in self.definition.designs()]
        if not designs or context.member is None:
            return {}
        try:
            return dict(await generate_design_previews(context.member, designs))
        except Exception:
            return {}

    def extra_buttons(self, context: OpenContext) -> tuple[Button, ...]:
        """The preview button."""
        locale = context.locale
        return (
            Button(
                text("buttons.preview.label", locale),
                "aside:preview",
                "secondary",
                "👁️",
                description=text("buttons.preview.desc", locale),
            ),
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """The preview renders the welcome message for the admin themself."""

        async def preview(interaction: Any, responses: Any) -> None:
            await send_welcome_message_preview(interaction, list(responses))

        return {
            "preview": AsideAction(
                preview, defer=True, cooldown=view_constants.ACTION_COOLDOWN_SECONDS
            )
        }


FEATURE = WelcomeMessagesFeature()

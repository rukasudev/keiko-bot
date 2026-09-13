"""Welcome messages: design previews before the gallery, a preview button after."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.forms.definitions.schema import SingleChoiceStep
from app.forms.engine.screen import Button
from app.forms.extensions.copy import text
from app.forms.features.generic import GenericCogFeature
from app.forms.features.protocol import AsideAction, OpenContext, Opened
from app.services.welcome_messages import (
    generate_design_previews,
    send_welcome_message_preview,
)


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
            )
        return Opened(previews=await self.previews(context))

    async def previews(self, context: OpenContext) -> Mapping[str, str]:
        """One rendered banner per design, for the member opening the form."""
        gallery = next(
            (
                step
                for step in self.definition.steps
                if isinstance(step, SingleChoiceStep) and step.designs
            ),
            None,
        )
        if gallery is None or context.member is None:
            return {}
        designs = [{"key": design.key} for design in gallery.designs]
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

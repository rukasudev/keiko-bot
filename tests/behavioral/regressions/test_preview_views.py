"""A preview with a single written message still opens.

On the local bot (2026-09-16) previewing a YouTube notification failed with
"expected view parameter to be of type View or LayoutView, not NoneType":
with nothing to page through, the sender passed `view=None`, which discord.py
refuses. Guaranteed: a preview never hands discord.py a None view.
"""
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.views.message_preview import MessagePreviewView

pytestmark = pytest.mark.behavioral


def _interaction():
    interaction = MagicMock()
    interaction.locale = "pt-br"
    interaction.followup.send = AsyncMock()
    return interaction


async def test_a_preview_with_one_message_never_sends_a_none_view():
    interaction = _interaction()

    await MessagePreviewView(["está ao vivo!"], "pt-br").send(interaction)

    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs.get("view", discord.utils.MISSING) is not None
    assert kwargs["content"] == "está ao vivo!"


async def test_the_welcome_preview_with_one_message_never_sends_a_none_view():
    from app.services.welcome_messages import send_welcome_message_preview
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    member = create_member(guild, id=555, name="Tester")
    member._user = MagicMock(id=555)
    interaction = _interaction()
    interaction.user = member
    interaction.guild = guild

    await send_welcome_message_preview(
        interaction,
        [
            {"key": "welcome_messages_title", "value": "Chegou gente nova!"},
            {"key": "welcome_messages", "value": "Oi {user}!"},
            {"key": "welcome_messages_footer", "value": "Divirta-se!"},
            {"key": "welcome_design", "value": "custom_only"},
            {
                "key": "welcome_custom_image",
                "value": "https://cdn.discordapp.com/attachments/1/2/banner.png",
            },
        ],
    )

    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs.get("view", discord.utils.MISSING) is not None


async def test_a_preview_with_nothing_written_still_says_something():
    """Broke as: Discord refused the preview with "Cannot send an empty
    message" when the feature handed it no text at all."""
    interaction = _interaction()

    await MessagePreviewView([], "pt-br").send(interaction)

    assert interaction.followup.send.await_args.kwargs["content"].strip()


async def test_a_preview_with_several_messages_carries_the_next_button():
    interaction = _interaction()

    await MessagePreviewView(["um", "dois"], "pt-br").send(interaction)

    kwargs = interaction.followup.send.await_args.kwargs
    assert isinstance(kwargs["view"], MessagePreviewView)

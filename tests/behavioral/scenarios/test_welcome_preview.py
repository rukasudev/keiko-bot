"""The welcome preview: every written message, with the chosen design."""
from unittest.mock import MagicMock

import pytest

from app.services.welcome_messages import welcome_preview_pages
from app.views.message_preview import MessagePreviewView
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

def _pressed():
    from unittest.mock import AsyncMock

    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()
    return interaction


SETTINGS = {
    "title": "Chegou gente nova!",
    "footer": "Divirta-se!",
    "design": "custom_only",
    "custom_image": "https://cdn.discordapp.com/attachments/1/2/banner.png",
}


async def test_the_preview_walks_through_every_written_message():
    """The preview renders the message as it will arrive, and the button moves
    to the next of the three written examples."""
    guild = create_guild()
    member = create_member(guild, id=555, name="Tester")
    member._user = MagicMock(id=555)
    pages = await welcome_preview_pages(
        member,
        ["Oi {user}!", "Olha quem chegou", "Chegou mais alguém!"],
        SETTINGS,
    )
    view = MessagePreviewView(pages, "pt-br")

    first = view.page
    await view.next_message.callback(_pressed())
    second = view.page

    assert len(pages) == 3
    assert first.title == SETTINGS["title"]
    assert "Oi <@!555>" in first.description
    assert "Olha quem chegou" in second.description
    assert first.image.url == SETTINGS["custom_image"]


async def test_the_next_button_never_redraws_the_banner(monkeypatch):
    """Broke as: Next awaited create_banner before answering the interaction,
    and Discord gives three seconds while the repo's own PREVIEW_WAIT_SECONDS
    records that the render takes eight."""
    import app.services.welcome_messages as welcome

    guild = create_guild()
    member = create_member(guild, id=555, name="Tester")
    member._user = MagicMock(id=555)
    pages = await welcome_preview_pages(
        member, ["Oi {user}!", "Olha quem chegou"], SETTINGS
    )
    view = MessagePreviewView(pages, "pt-br")
    drawn = []
    monkeypatch.setattr(
        welcome, "create_banner", lambda *args, **kwargs: drawn.append(args)
    )

    await view.next_message.callback(_pressed())

    assert drawn == []
    assert view.page is pages[1]

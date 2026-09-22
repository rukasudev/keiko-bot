"""The welcome preview: every written message, with the chosen design."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.welcome_messages import welcome_preview_pages
from app.views.message_preview import MessagePreviewView
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

PREVIEW_URL = "https://cdn.example.com/previews/welcome-preview.png"

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


async def test_the_card_shows_the_banner_of_the_chosen_design(scenario_factory):
    """Lucas: the card named the design and showed nothing else. The design line
    now carries the banner it draws, with the button to change it under it."""
    with patch(
        "app.services.welcome_messages.create_banner",
        new=AsyncMock(return_value=PREVIEW_URL),
    ):
        scenario = await scenario_factory(locale="pt-br").start("welcome_messages")
        await scenario.confirm()
        card = scenario.expect_message(components_v2=True)

    drawn = card["components"][0]["children"]
    shown = next(i for i, part in enumerate(drawn) if part["type"] == "media")
    assert drawn[shown - 1]["content"] == "> Imagem do Servidor"
    assert drawn[shown + 1]["children"][0]["label"] == "Escolher design"
    await scenario.finish()


async def test_a_blurred_design_draws_the_banner_over_the_sent_picture(
    scenario_factory, deps
):
    """Lucas: the card showed the picture itself instead of the banner drawn
    over it, which is not what a member receives. The designs that blur a
    background are drawn again with the picture the admin just sent."""
    import discord

    from tests.behavioral.harness.locators import walk_items

    uploaded = "https://cdn.discordapp.com/attachments/999/1/custom-banner.png"
    drawn_over = f"{PREVIEW_URL}?drawn"

    async def send(*_args, **_kwargs):
        return SimpleNamespace(attachments=[SimpleNamespace(url=uploaded)])

    async def banner(background, *_args, **_kwargs):
        return drawn_over if background == uploaded else PREVIEW_URL

    deps.bot.get_channel = lambda _channel_id: SimpleNamespace(send=send)
    with patch("app.services.welcome_messages.create_banner", new=banner):
        scenario = await scenario_factory(locale="pt-br").start("welcome_messages")
        await scenario.confirm()
        await scenario.click("customize:2")
        await scenario.click("design:custom_blur")
        await scenario.click("customize:3")
        await scenario.submit_file_upload(
            filename="custom-banner.png", content=b"\x89PNG-fake"
        )

    shown = [
        media.media.url
        for item in walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.MediaGallery)
        for media in item.items
    ]
    assert shown[0] == drawn_over, shown
    assert uploaded in shown[1:], "the upload section still shows the file itself"
    await scenario.finish()


async def test_the_card_shows_the_picture_that_was_just_sent(scenario_factory, deps):
    """Lucas: after sending a custom image the banner on the card has to change.
    A design drawn over the admin's own picture shows that picture, not the
    example of the design."""
    import discord

    from tests.behavioral.harness.locators import walk_items

    uploaded = "https://cdn.discordapp.com/attachments/999/1/custom-banner.png"

    async def send(*_args, **_kwargs):
        return SimpleNamespace(attachments=[SimpleNamespace(url=uploaded)])

    deps.bot.get_channel = lambda _channel_id: SimpleNamespace(send=send)
    with patch(
        "app.services.welcome_messages.create_banner",
        new=AsyncMock(return_value=PREVIEW_URL),
    ):
        scenario = await scenario_factory(locale="pt-br").start("welcome_messages")
        await scenario.confirm()
        await scenario.click("customize:2")
        await scenario.click("design:custom_only")
        await scenario.click("customize:3")
        await scenario.submit_file_upload(
            filename="custom-banner.png", content=b"\x89PNG-fake"
        )

    drawn = [
        shown.media.url
        for item in walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.MediaGallery)
        for shown in item.items
    ]
    assert uploaded in drawn, drawn
    await scenario.finish()


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

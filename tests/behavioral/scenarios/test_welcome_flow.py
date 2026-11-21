"""Behavioral scenarios: /moderações mensagens boas-vindas, its card and its preview.

Guaranteed: the card draws the banner of the chosen design over the picture the
admin just sent (or that picture itself), asks for the picture only for a custom
design and refuses a file the banner could not draw without keeping it; the preview
walks every written message as it will arrive, over one banner drawn and uploaded
once, and its Next button never draws the banner again.
"""
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.commands.welcome import service as welcome_service
from app.commands.welcome.service import WelcomeSettings, welcome_preview_pages
from app.core.utils import ml
from app.ui.message_preview import MessagePreviewView
from tests.behavioral.flows import welcome
from tests.behavioral.flows.common import UPLOADED_URL, dump_channel
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

SETTINGS = WelcomeSettings(
    title="Chegou gente nova!",
    footer="Divirta-se!",
    design="custom_only",
    custom_image="https://cdn.discordapp.com/attachments/1/2/banner.png",
)


def _pressed():
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()
    return interaction


def _gallery(scenario):
    return [
        shown.media.url
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.MediaGallery)
        for shown in item.items
    ]


def _required_fields():
    return ml("errors.required_fields.message", "pt-br").split("$")[0]


def _member():
    member = create_member(create_guild(), id=555, name="Tester")
    member._user = MagicMock(id=555)
    return member


async def _card_with_its_picture(scenario_factory, design):
    scenario = await welcome.open_card(scenario_factory, "pt-br")
    await scenario.select_option(design, target="welcome_design")
    await scenario.click("customize:image")
    await scenario.submit_file_upload(filename="custom-banner.png", content=b"\x89PNG-fake")
    return scenario


async def test_a_blurred_design_draws_the_banner_over_the_sent_picture(
    scenario_factory, deps, monkeypatch
):
    """Lucas: the card showed the picture itself instead of the banner drawn
    over it, which is not what a member receives. The designs that blur a
    background are drawn again with the picture the admin just sent."""
    drawn_over = f"{welcome.PREVIEW_URL}?drawn"

    async def banner(background, *_args, **_kwargs):
        return drawn_over if background == UPLOADED_URL else welcome.PREVIEW_URL

    dump_channel(deps)
    monkeypatch.setattr(welcome_service, "create_banner", banner)
    scenario = await _card_with_its_picture(scenario_factory, "custom_blur")

    shown = _gallery(scenario)
    assert shown[0] == drawn_over, shown
    assert UPLOADED_URL in shown[1:], "the upload section still shows the file itself"
    await scenario.finish()


async def test_the_card_shows_the_picture_that_was_just_sent(
    scenario_factory, deps, monkeypatch
):
    """Lucas: after sending a custom image the banner on the card has to change.
    A design drawn over the admin's own picture shows that picture, not the
    example of the design."""
    dump_channel(deps)
    monkeypatch.setattr(
        welcome_service, "create_banner", AsyncMock(return_value=welcome.PREVIEW_URL)
    )
    scenario = await _card_with_its_picture(scenario_factory, "custom_only")

    assert UPLOADED_URL in _gallery(scenario), _gallery(scenario)
    await scenario.finish()


async def test_a_welcome_custom_image_is_required_only_for_custom_designs(
    scenario_factory, deps
):
    """The image section only shows for a custom design, and Done asks for it."""
    dump_channel(deps)
    scenario = await welcome.open_card(scenario_factory, "pt-br")
    await scenario.select_option("custom_only", target="welcome_design")

    await scenario.click("done")
    scenario.expect_error(_required_fields())

    await scenario.click("customize:image")
    await scenario.submit_file_upload(filename="banner.png", content=b"\x89PNG-fake")
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.finish()


@pytest.mark.parametrize(
    "upload",
    [
        {"size": 11 * 1024 * 1024},
        {"width": 5000, "height": 5000},
        {"filename": "page.html", "content_type": "text/html"},
    ],
    ids=["too-heavy", "too-many-pixels", "not-a-picture"],
)
async def test_a_file_the_banner_would_refuse_is_told_and_never_kept(
    scenario_factory, deps, upload
):
    """The welcome banner draws only a PNG, JPG, GIF or WEBP of up to 10 MB and
    4096 x 4096 pixels: any other file is refused on a message of its own, is
    never re-uploaded to the dump channel, and the card stays as it was."""
    dumped = dump_channel(deps)
    scenario = await welcome.open_card(scenario_factory, "pt-br")
    await scenario.select_option("custom_only", target="welcome_design")
    await scenario.click("customize:image")
    before = dict(scenario.card_state)

    await scenario.submit_file_upload(**upload)

    scenario.expect_error(ml("errors.image_not_accepted.title", "pt-br"))
    assert dumped.sent == [], "a refused file never reaches the dump channel"
    assert scenario.card_state == before
    scenario.expect_step("welcome_config")

    await scenario.click("done")
    scenario.expect_error(_required_fields())

    await scenario.click("customize:image")
    await scenario.submit_file_upload(filename="banner.png")
    assert dumped.sent and UPLOADED_URL in scenario.card_state.values()
    await scenario.finish()


async def test_the_preview_walks_through_every_written_message():
    """The preview renders the message as it will arrive, and the button moves
    to the next of the three written examples."""
    pages = await welcome_preview_pages(
        _member(), ["Oi {user}!", "Olha quem chegou", "Chegou mais alguém!"], SETTINGS
    )
    view = MessagePreviewView(pages, "pt-br")

    first = view.page
    await view.next_message.callback(_pressed())
    second = view.page

    assert len(pages) == 3
    assert first.title == SETTINGS.title
    assert "Oi <@!555>" in first.description
    assert "Olha quem chegou" in second.description
    assert first.image.url == SETTINGS.custom_image


async def test_the_next_button_never_redraws_the_banner(monkeypatch):
    """Broke as: Next awaited create_banner before answering the interaction,
    and Discord gives three seconds while the repo's own PREVIEW_WAIT_SECONDS
    records that the render takes eight."""
    pages = await welcome_preview_pages(
        _member(), ["Oi {user}!", "Olha quem chegou"], SETTINGS
    )
    view = MessagePreviewView(pages, "pt-br")
    drawn = []
    monkeypatch.setattr(
        welcome_service, "create_banner", lambda *args, **kwargs: drawn.append(args)
    )

    await view.next_message.callback(_pressed())

    assert drawn == []
    assert view.page is pages[1]


async def test_a_drawn_design_previews_every_message_over_one_banner(monkeypatch):
    """The preview keeps the dump channel: its Next button edits the message,
    and an attached banner would have to travel again on every edit. Every
    page is the same banner, so it is drawn and uploaded once."""
    banner = AsyncMock(return_value=welcome.PREVIEW_URL)
    monkeypatch.setattr(welcome_service, "create_banner", banner)

    pages = await welcome_preview_pages(
        _member(),
        ["Oi {user}!", "Olha quem chegou", "Chegou mais alguém!"],
        replace(SETTINGS, design="server_blur"),
    )

    assert [page.image.url for page in pages] == [welcome.PREVIEW_URL] * 3
    assert banner.await_count == 1
    assert pages[0].title == SETTINGS.title

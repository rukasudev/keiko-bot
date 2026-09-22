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


async def test_the_twitch_preview_carries_the_embed_the_notification_sends(deps):
    """Lucas: the preview should be the very embed the server gets, not the
    text alone. The live's own title and category fill it when there is one."""
    from app.services.notifications_twitch import send_notification_preview

    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.set_stream_online("gaules", game="CS2", title="Bom dia!")
    interaction = _interaction()

    await send_notification_preview(
        interaction,
        [
            {"key": "streamer", "value": "gaules"},
            {"key": "notification_messages", "value": "está ao vivo!"},
        ],
    )

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "Bom dia!"
    assert [field.value for field in embed.fields] == ["CS2", "gaules"]
    assert embed.thumbnail.url, "the streamer picture belongs on the embed"


async def test_the_twitch_preview_shows_an_example_when_nobody_is_live(deps):
    """Lucas: show something where the live picture goes, so the space it takes
    is visible. A streamer who is offline has no title, category or picture of
    the moment, so the example lines and the last stream's picture stand in."""
    from app.services.notifications_twitch import send_notification_preview
    from app.services.utils import ml

    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.set_last_video(
        "gaules", "https://static-cdn.jtvnw.net/vod/%{width}x%{height}.jpg"
    )
    interaction = _interaction()

    await send_notification_preview(
        interaction,
        [
            {"key": "streamer", "value": "gaules"},
            {"key": "notification_messages", "value": "está ao vivo!"},
        ],
    )

    embed = interaction.followup.send.await_args.kwargs["embed"]
    example = ml(
        "commands.commands.commons.notifications-preview.twitch.title", locale="pt-br"
    )
    assert embed.title == example
    assert embed.image.url == "https://static-cdn.jtvnw.net/vod/1280x720.jpg"


async def test_the_youtube_preview_carries_the_embed_the_notification_sends(
    deps, monkeypatch
):
    """The channel is the real one; the video lines show an example, since the
    next video does not exist yet.

    The channel answer is written here in the shape the real client returns
    (the snippet itself); the YouTube mock wraps it in one more level.
    """
    from app.services import notifications_youtube_video as youtube
    from app.services.utils import ml

    snippet = {
        "title": "Canal de Teste",
        "description": "O canal de testes\nsegunda linha",
        "customUrl": "@pewdiepie",
        "thumbnails": {"high": {"url": "https://yt3.ggpht.com/avatar.jpg"}},
    }
    monkeypatch.setattr(deps.youtube, "get_channel_id_from_username", lambda _n: "UC1")
    monkeypatch.setattr(deps.youtube, "get_channel_info", lambda _id: snippet)
    interaction = _interaction()

    await youtube.send_notification_preview(
        interaction,
        [
            {"key": "youtuber", "value": "pewdiepie"},
            {"key": "notification_messages", "value": "postou vídeo novo!"},
        ],
    )

    embed = interaction.followup.send.await_args.kwargs["embed"]
    example = ml(
        "commands.commands.commons.notifications-preview.youtube.title", locale="pt-br"
    )
    assert embed.title == example
    assert embed.thumbnail.url == "https://yt3.ggpht.com/avatar.jpg"
    assert embed.description == "O canal de testes"


async def test_a_preview_with_several_messages_carries_the_next_button():
    interaction = _interaction()

    await MessagePreviewView(["um", "dois"], "pt-br").send(interaction)

    kwargs = interaction.followup.send.await_args.kwargs
    assert isinstance(kwargs["view"], MessagePreviewView)

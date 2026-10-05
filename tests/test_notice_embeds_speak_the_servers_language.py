"""A live or video notice names its fields in the language of the server it reaches.

Reported in the v1 review of the loop and runtime PR: once the Twitch status footer came
from the language files, four field names were still English written in Python: the
Twitch notice's "Game" and "Streamer", the YouTube notice's "Description" and "Tags".

Guaranteed: the field names come from `commands.commons.notifications-fields` in the
locale the notice is written in, both locales carry them, and English reads as before.
"""
import pytest

from app.services.notifications_twitch import create_stream_notification_embed
from app.services.notifications_youtube_video import create_video_notification_embed

pytestmark = pytest.mark.unit

VIDEO = {"title": "New video", "description": "What happened today", "tags": ["vlog", "dog"]}
CHANNEL = {"description": "A channel", "thumbnails": {}}


def field_names(embed):
    return [field.name for field in embed.fields]


@pytest.mark.parametrize(
    "locale,names", [("en-us", ["Game", "Streamer"]), ("pt-br", ["Categoria", "Streamer"])]
)
def test_a_live_notice_names_its_fields_in_the_servers_language(twitch, locale, names):
    twitch.add_user("gaules", user_id="123")
    twitch.set_stream_online("gaules", game="CS2", title="Live Test!")

    embed = create_stream_notification_embed(
        "gaules", twitch.get_stream_info("gaules"), twitch.get_user_info("gaules"), locale
    )

    assert field_names(embed) == names


@pytest.mark.parametrize(
    "locale,names", [("en-us", ["Description", "Tags"]), ("pt-br", ["Descrição", "Tags"])]
)
def test_a_video_notice_names_its_fields_in_the_servers_language(locale, names):
    embed = create_video_notification_embed("video-1", VIDEO, CHANNEL, locale)

    assert field_names(embed) == names

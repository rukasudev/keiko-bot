"""The admin's YouTube message is printed as typed, apart from its two placeholders.

Reported in the v1 review (3.2): `parse_streamer_message` ran `str.format` over the
template an admin types. A width specifier such as `{youtuber:>999999999}` made the
process allocate gigabytes on a 3.6 GB VPS, and the Preview button made that one click
for any admin of any server; attributes (`{youtuber.__class__}`) and indexes
(`{video_link[0]}`) were evaluated too, and a placeholder it did not know made the real
announcement raise instead of posting.

Found during the PR review: the fix copied the Twitch renderer under the same name, and
both decided whether a link was present ignoring case while replacing only the lowercase
placeholder, so `{VIDEO_LINK}` stopped the link from being added and was never replaced.

Shared behaviour: the renderer behind both the announcement and the Preview, now the one
`fill_placeholders` the Twitch notifier uses too.
Exposed by: any server admin, since anyone can create a server and add Keiko.

Guaranteed: only `{youtuber}` and `{video_link}` are replaced, everything else stays as
typed, the link is added whenever `{video_link}` itself is missing, and a message is never
longer than the template, the two values and that link.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.notifications_youtube_video import (
    compose_notification_message,
    create_video_notification_embed,
    parse_streamer_message,
    send_notification_preview,
)

pytestmark = [pytest.mark.unit, pytest.mark.regression]

LINK = "https://www.youtube.com/watch?v=video-1"
WIDTH = "{youtuber:>100000}"


@pytest.mark.parametrize("template", [
    WIDTH,
    "{video_link:^100000}",
    "{youtuber.__class__}",
    "{video_link[0]}",
    "{{literal}}",
    "{VIDEO_LINK}",
    "{unknown}",
], ids=["width", "centered-width", "attribute", "index", "doubled-braces",
        "capitalised-placeholder", "unknown-placeholder"])
def test_anything_but_the_two_placeholders_stays_as_typed(template):
    message = parse_streamer_message(template, "pewdiepie", LINK)

    assert message.startswith(template)
    assert len(message) <= len(template) + len("\n") + len(LINK)


def test_the_two_placeholders_are_still_filled():
    message = parse_streamer_message("{youtuber} posted: {video_link}", "pewdiepie", LINK)

    assert message == f"pewdiepie posted: {LINK}"


def test_the_link_is_still_added_when_the_template_has_none():
    assert parse_streamer_message("New video!", "pewdiepie", LINK) == f"New video!\n{LINK}"


def test_a_capitalised_placeholder_is_printed_as_typed_and_the_link_still_added():
    message = parse_streamer_message("{VIDEO_LINK}", "pewdiepie", LINK)

    assert message == f"{{VIDEO_LINK}}\n{LINK}"


def test_the_announcement_of_a_width_specifier_stays_small():
    notification = {"notification_messages": {"value": WIDTH}}

    message = compose_notification_message(notification, "pewdiepie", "video-1")

    assert message == f"{WIDTH}\n{LINK}"


async def test_the_preview_of_a_width_specifier_stays_small(deps, monkeypatch):
    """The Preview button is the path that made it one click."""
    monkeypatch.setattr(deps.youtube, "get_channel_id_from_username", lambda _name: None)
    interaction = MagicMock()
    interaction.locale = "en-US"
    interaction.followup.send = AsyncMock()

    await send_notification_preview(interaction, [
        {"key": "youtuber", "value": "pewdiepie"},
        {"key": "notification_messages", "value": WIDTH},
    ])

    content = interaction.followup.send.await_args.kwargs["content"]
    assert content == f"{WIDTH}\nhttps://www.youtube.com/@pewdiepie"


VIDEO = {"title": "New video", "description": "What happened today", "tags": ["vlog", "dog"]}


CHANNEL = {"description": "A channel", "thumbnails": {}}


def field_names(embed):
    return [field.name for field in embed.fields]


@pytest.mark.parametrize(
    "locale,names", [("en-us", ["Description", "Tags"]), ("pt-br", ["Descrição", "Tags"])]
)
def test_a_video_notice_names_its_fields_in_the_servers_language(locale, names):
    """A live or video notice names its fields in the language of the server it reaches.

    Reported in the v1 review of the loop and runtime PR: once the Twitch status footer came
    from the language files, four field names were still English written in Python: the
    Twitch notice's "Game" and "Streamer", the YouTube notice's "Description" and "Tags".

    Guaranteed: the field names come from `commands.commons.notifications-fields` in the
    locale the notice is written in, both locales carry them, and English reads as before.
    """
    embed = create_video_notification_embed("video-1", VIDEO, CHANNEL, locale)

    assert field_names(embed) == names

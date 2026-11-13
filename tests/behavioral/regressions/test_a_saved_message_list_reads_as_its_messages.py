"""Saved notification messages read as the messages they are, in any stored shape.

Found in the round-6 prints (2026-11-06): Editar on a saved YouTube channel, then
Pré-visualizar, sent "['@everyone! pewdiepie acabou de postar um novo vídeo!']" with
the link below it. The item held its messages as a list, and the preview read them
with `str(...).split(";")`, which only knows the `;`-joined string the card saves.
The Twitch preview and both real notifiers read the same value the same way: a list
became its Python repr in the preview and an AttributeError in the notifier.

Shared behaviour: the one helper that turns a saved message value into the messages
a sender picks from (`split_messages` in `app/services/utils.py`), read by the
Twitch and YouTube previews (the card's and the panel's Pré-visualizar) and by the
live and new video announcements. Consumers that exposed it: notifications_youtube_video
and notifications_twitch, whose saved items may hold a list or a `;`-joined string.

Welcome messages read the same value with their own `split_welcome_messages`
(`str.split(";")` again), so a welcome saved as a list crashed both the welcome a
new member gets and the panel's Pré-visualizar with an AttributeError (review
fixes K2, item 5); they now read it through `split_messages` too.

Guaranteed: a saved list and a `;`-joined string both preview as one message per
entry, filled, with no brackets or quotes, two entries as two pages, and a real
announcement or welcome of either shape sends one of those messages.
"""
import copy
import random
from unittest.mock import AsyncMock, patch

import pytest

from app.core.utils import ml

from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths import notifications_youtube_video as youtube
from tests.behavioral.golden.paths import welcome_messages as welcome
from tests.behavioral.golden.paths.common import open_manager, seed_document
from tests.generators.moderations import recorded_server
from tests.mocks.discord import create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]

YOUTUBE_PREVIEW = (
    "@everyone! pewdiepie acabou de postar um novo vídeo!\n"
    "https://www.youtube.com/@pewdiepie"
)
TWITCH_PREVIEW = "@everyone gaules está ao vivo! https://www.twitch.tv/gaules"
CASES = [
    pytest.param(youtube, YOUTUBE_PREVIEW, id="youtube"),
    pytest.param(twitch, TWITCH_PREVIEW, id="twitch"),
]


async def _preview_content(scenario, target):
    seen = len(scenario.outputs)
    await scenario.click(target)
    sent = [
        event for event in scenario.outputs[seen:]
        if event["actor"] == "bot" and event["kind"] in ("send", "followup_send")
    ]
    assert len(sent) == 1, scenario.transcript
    return sent[0].get("content")


@pytest.mark.parametrize("feature, expected", CASES)
async def test_an_item_edit_previews_the_saved_messages_as_text(
    scenario_factory, deps, feature, expected
):
    scenario = await open_manager(
        scenario_factory, deps, "pt-br", feature.FORM, feature._seed
    )
    await scenario.click("section:notifications$0")

    content = await _preview_content(scenario, "preview")

    assert content == expected, scenario.transcript
    await scenario.finish()


@pytest.mark.parametrize("feature, expected", CASES)
async def test_the_panel_previews_a_saved_item_as_text(
    scenario_factory, deps, feature, expected
):
    scenario = await open_manager(
        scenario_factory, deps, "pt-br", feature.FORM, feature._seed
    )

    content = await _preview_content(scenario, "aside:preview:notifications$0")

    assert content == expected, scenario.transcript
    await scenario.finish()


TWO_MESSAGES = [
    pytest.param(
        twitch, twitch._known_streamers, "streamer",
        "gaules", "https://www.twitch.tv/gaules", id="twitch",
    ),
    pytest.param(
        youtube, youtube._known_channels, "youtuber",
        "pewdiepie", "https://www.youtube.com/@pewdiepie", id="youtube",
    ),
]


def _stored(shape, entries):
    return list(entries) if shape == "list" else "; ".join(entries)


@pytest.mark.parametrize("shape", ["list", "joined"])
@pytest.mark.parametrize("feature, known, placeholder, name, link", TWO_MESSAGES)
async def test_two_saved_messages_preview_as_two_pages(
    scenario_factory, deps, shape, feature, known, placeholder, name, link
):
    entries = (f"Primeira de {{{placeholder}}}!", f"Segunda de {{{placeholder}}}!")
    document = copy.deepcopy(feature.ENABLED)
    item = document["notifications"]["values"][0]
    item["notification_messages"]["value"] = _stored(shape, entries)

    def seed(deps):
        known(deps)
        seed_document(deps, feature.FORM, document)

    scenario = await open_manager(scenario_factory, deps, "pt-br", feature.FORM, seed)

    content = await _preview_content(scenario, "aside:preview:notifications$0")

    expected = [f"Primeira de {name}!\n{link}", f"Segunda de {name}!\n{link}"]
    assert content == expected[0], scenario.transcript
    preview = scenario.current_message
    assert [item.label for item in preview.view.children] == ["Próxima mensagem"]
    assert preview.view.pages == expected
    await scenario.finish()


def _destination(deps, name):
    guild = create_guild(id=5001, channels=[name])
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    recorded_server(deps.mongo_client, guild.id)
    return guild, guild.text_channels[0]


def _sent(channel):
    return channel._send.await_args.kwargs["content"]


def _picking_the_last(monkeypatch):
    offered = []

    def last(entries):
        offered.append(list(entries))
        return entries[-1]

    monkeypatch.setattr(random, "choice", last)
    return offered


VIDEO_MESSAGES = [
    pytest.param(["{youtuber} postou {video_link}"], 1, id="list"),
    pytest.param("{youtuber} postou {video_link}", 1, id="joined"),
    pytest.param(["{youtuber} chegou", "{youtuber} postou {video_link}"], 2, id="list-of-two"),
    pytest.param("{youtuber} chegou; {youtuber} postou {video_link}", 2, id="joined-two"),
]
LIVE_MESSAGES = [
    pytest.param(["{streamer} on! {stream_link}"], 1, id="list"),
    pytest.param("{streamer} on! {stream_link}", 1, id="joined"),
    pytest.param(["{streamer} chegou", "{streamer} on! {stream_link}"], 2, id="list-of-two"),
    pytest.param("{streamer} chegou; {streamer} on! {stream_link}", 2, id="joined-two"),
]


@pytest.mark.parametrize("stored, count", VIDEO_MESSAGES)
async def test_a_new_video_is_announced_with_one_saved_message(
    deps, monkeypatch, stored, count
):
    from app.services.notifications_youtube_video import VideoAnnouncement, deliver_video

    guild, channel = _destination(deps, "videos")
    announcement = VideoAnnouncement(
        video={"id": "video-1", "snippet": {"title": "A new video"}},
        channel={"title": "PewDiePie"},
        youtuber="pewdiepie",
        followers=[],
    )
    notification = {
        "youtuber": {"value": "pewdiepie"},
        "channel": {"value": str(channel.id)},
        "notification_messages": {"value": stored, "style": "bullet"},
    }

    offered = _picking_the_last(monkeypatch)

    await deliver_video(announcement, {"guild_id": str(guild.id)}, notification)

    assert [len(entries) for entries in offered] == [count]
    assert _sent(channel) == (
        "pewdiepie postou https://www.youtube.com/watch?v=video-1"
    )


@pytest.mark.parametrize("stored, count", LIVE_MESSAGES)
async def test_a_live_is_announced_with_one_saved_message(
    deps, monkeypatch, stored, count
):
    from app.services.notifications_twitch import handle_send_streamer_notification

    guild, channel = _destination(deps, "lives")
    deps.twitch.add_user("gaules", user_id="123")
    deps.twitch.set_stream_online("gaules")
    deps.mongo_client.guild.notifications_twitch.insert_one({
        "guild_id": str(guild.id),
        "enabled": True,
        "notifications": {"values": [{
            "streamer": {"value": "gaules"},
            "channel": {"value": str(channel.id)},
            "notification_messages": {"value": stored, "style": "bullet"},
        }]},
    })

    offered = _picking_the_last(monkeypatch)

    await handle_send_streamer_notification("gaules")

    assert [len(entries) for entries in offered] == [count]
    assert _sent(channel) == "gaules on! https://www.twitch.tv/gaules"


WELCOMES = ["{user} chegou ao {server}!", "Olha quem chegou! {user}"]
WELCOME_SHAPES = pytest.mark.parametrize(
    "stored", [WELCOMES, ";".join(WELCOMES)], ids=["list", "joined"]
)


def _welcome_seed(stored):
    document = {
        **welcome.ENABLED,
        "welcome_messages": {"style": "bullet", "values": stored},
    }
    return lambda deps: deps.mongo_client.guild.welcome_messages.insert_one(document)


@WELCOME_SHAPES
async def test_the_welcome_panel_previews_each_saved_message(
    scenario_factory, deps, stored
):
    with welcome._banner_stub():
        scenario = await open_manager(
            scenario_factory, deps, "pt-br", welcome.FORM, _welcome_seed(stored)
        )
        scenario.user._user = scenario.user
        seen = len(scenario.outputs)
        await scenario.click("aside:preview")

    sent = [
        event for event in scenario.outputs[seen:]
        if event["actor"] == "bot" and event["kind"] in ("send", "followup_send")
    ]
    assert len(sent) == 1, scenario.transcript
    assert sent[0]["embed"]["description"] == "<@!555> chegou ao Test Server!"
    pages = [item["label"] for item in sent[0]["components"]]
    assert pages == [ml("buttons.preview.next", locale="pt-br")], pages
    await scenario.finish()


@WELCOME_SHAPES
async def test_a_new_member_is_welcomed_with_one_saved_message(deps, stored):
    from app.services.welcome_messages import send_welcome_message

    guild, channel = _destination(deps, "boas-vindas")
    member = create_member(guild, id=999, name="Novata")
    member._user = member
    deps.mongo_client.guild.welcome_messages.insert_one({
        **welcome.ENABLED,
        "guild_id": str(guild.id),
        "welcome_messages_channel": {"style": "channel", "values": str(channel.id)},
        "welcome_messages": {"style": "bullet", "values": stored},
    })

    with patch(
        "app.services.welcome_messages.create_banner",
        new=AsyncMock(return_value=welcome.PREVIEW_URL),
    ):
        await send_welcome_message(member)

    description = channel._send.await_args.kwargs["embed"].description
    assert description in (
        f"<@!999> chegou ao {guild.name}!",
        "Olha quem chegou! <@!999>",
    )

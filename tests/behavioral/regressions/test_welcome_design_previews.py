"""The welcome design gallery and banners: light examples, no outside downloads.

What broke (found on the local bot and in production data, 2026-09-14):

- the "custom image only" example was a 28.3 MB, 273-frame gif on i.ibb.co that
  Discord fetched for every admin who opened the gallery: slow, and at times
  "image not found";
- the "custom with blur" example downloaded a 1 MB gif from the same host (about
  5 s) to use its first frame, and the previews were drawn one after the other;
- a server without an icon took its background from i.sstatic.net, which
  answers 403 to every client, so the banner could not be drawn;
- a custom background is saved as a Discord attachment link, which stops
  downloading 24 hours after it was signed, so `custom_blur` banners failed.

Shared behaviour: `create_banner`, `generate_design_previews`, image downloads
and dump channel uploads. Consumers: the welcome setup gallery and every member
joining a server with welcome messages. Guaranteed: the gallery shows every
example without downloading from outside Discord, and a member always gets a
banner.
"""
import asyncio
import os
import socket
import threading
import time
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import pytest
import requests
from PIL import Image

from app.constants import WelcomeDesign
from app.services import cdn, welcome_messages
from tests.mocks import create_member

pytestmark = [pytest.mark.behavioral]

REAL_CREATE_BANNER = welcome_messages.create_banner
REAL_UPLOAD_ASSET = cdn.upload_asset
UPLOADED_URL = "https://cdn.discordapp.com/attachments/999/1/uploaded.png"
DESIGNS = [{"key": "server_blur"}, {"key": "custom_blur"}, {"key": "custom_only"}]
DISCORD_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
DISCORD_EMBED_LIMIT_BYTES = 8 * 1024 * 1024
EXPIRED = "https://cdn.discordapp.com/attachments/1/2/background.png?ex=69d5b0af&is=69d45f2f&hm=abc&"
REFRESHED = "https://cdn.discordapp.com/attachments/1/2/background.png?ex=7fffffff&is=7ffe0000&hm=def&"


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeWeb:
    """The internet as the bot sees it: Discord's CDN answers, other hosts refuse."""

    def __init__(self):
        self.requested = []
        self.answers = {}

    def get(self, url, *args, **kwargs):
        self.requested.append(url)
        default = 200 if urlparse(url).hostname in DISCORD_HOSTS else 403
        response = requests.Response()
        response.status_code = self.answers.get(url, default)
        response._content = _png() if response.status_code == 200 else b"<html>no</html>"
        response.url = url
        return response


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb()
    monkeypatch.setattr(requests, "get", fake.get)
    return fake


@pytest.fixture
def real_banners(monkeypatch):
    monkeypatch.setattr(welcome_messages, "create_banner", REAL_CREATE_BANNER)
    monkeypatch.setattr(cdn, "upload_asset", REAL_UPLOAD_ASSET)


@pytest.fixture
def dump_channel(bot):
    channel = bot.get_channel.return_value
    channel.send = AsyncMock(
        return_value=SimpleNamespace(attachments=[SimpleNamespace(url=UPLOADED_URL)])
    )
    return channel


def _newcomer(guild, member_id):
    member = create_member(guild, id=member_id, name="NewUser")
    member._user = MagicMock()
    member._user.id = member_id
    return member


def _welcome(channel, design, custom_image=None):
    config = {
        "welcome_messages_channel": {"values": str(channel.id)},
        "welcome_messages": {"values": "Welcome {user}!"},
        "welcome_messages_title": "Welcome!",
        "welcome_messages_footer": "Enjoy!",
        "welcome_design": design,
    }
    if custom_image:
        config["welcome_custom_image"] = custom_image
    return config


@contextmanager
def silent_server():
    """A host that accepts the connection and never says a word."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    accepted = []

    def serve():
        while True:
            try:
                accepted.append(listener.accept()[0])
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()
        for connection in accepted:
            connection.close()


async def test_the_custom_image_example_is_a_light_file_discord_serves_itself(
    deps, guild, dump_channel, real_banners, web
):
    member = _newcomer(guild, 7001)

    first = await welcome_messages.generate_design_previews(member, DESIGNS)
    second = await welcome_messages.generate_design_previews(member, DESIGNS)

    assert urlparse(first["custom_only"]).hostname in DISCORD_HOSTS
    assert second["custom_only"] == first["custom_only"]
    assert os.path.getsize(WelcomeDesign.CUSTOM_ONLY_PREVIEW) < DISCORD_EMBED_LIMIT_BYTES
    gif_uploads = [
        call for call in dump_channel.send.await_args_list
        if str(getattr(call.kwargs.get("file"), "filename", "")).endswith(".gif")
    ]
    assert len(gif_uploads) <= 1, "the example is uploaded once and reused"


async def test_the_gallery_downloads_nothing_from_outside_discord(
    deps, guild, dump_channel, real_banners, web
):
    member = _newcomer(guild, 7002)

    previews = await welcome_messages.generate_design_previews(member, DESIGNS)

    outside = [url for url in web.requested if urlparse(url).hostname not in DISCORD_HOSTS]
    assert outside == []
    assert set(previews) == {"server_blur", "custom_blur", "custom_only"}


async def test_the_previews_are_drawn_at_the_same_time(
    deps, guild, dump_channel, monkeypatch, web
):
    running = 0
    peak = 0

    async def slow_banner(*args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return UPLOADED_URL

    monkeypatch.setattr(welcome_messages, "create_banner", slow_banner)

    await welcome_messages.generate_design_previews(_newcomer(guild, 7003), DESIGNS)

    assert peak == 2, "the server and blur banners wait for each other"


async def test_a_server_without_an_icon_still_gets_a_banner(
    deps, guild, channel, dump_channel, real_banners, web, mock_cache, monkeypatch
):
    monkeypatch.setattr(type(guild), "icon", property(lambda self: None))
    mock_cache.return_value = _welcome(channel, "server_blur")

    await welcome_messages.send_welcome_message(_newcomer(guild, 7004))

    channel.assert_message_sent()
    assert channel.get_last_embed().image.url == UPLOADED_URL


async def test_an_expired_custom_background_is_refreshed_before_drawing(
    deps, guild, bot, channel, dump_channel, real_banners, web, mock_cache
):
    web.answers[EXPIRED] = 404
    bot.http.request = AsyncMock(
        return_value={"refreshed_urls": [{"original": EXPIRED, "refreshed": REFRESHED}]}
    )
    mock_cache.return_value = _welcome(channel, "custom_blur", EXPIRED)

    await welcome_messages.send_welcome_message(_newcomer(guild, 7005))

    channel.assert_message_sent()
    assert REFRESHED in web.requested


async def test_a_background_that_cannot_be_downloaded_still_gets_a_banner(
    deps, guild, channel, dump_channel, real_banners, web, mock_cache
):
    mock_cache.return_value = _welcome(channel, "custom_blur", "https://example.com/gone.png")

    await welcome_messages.send_welcome_message(_newcomer(guild, 7006))

    channel.assert_message_sent()
    assert channel.get_last_embed().image.url == UPLOADED_URL


async def test_a_banner_drawn_plain_says_so_where_it_can_be_counted(
    deps, guild, channel, dump_channel, real_banners, web, mock_cache, monkeypatch
):
    """A server whose design stopped being drawn must not read as a healthy one.

    The member still gets a welcome, so the only place the degradation can show
    is the value it records: a warning inside a listener never reaches a channel.
    """
    from app.services import analytics

    recorded = []
    monkeypatch.setattr(
        analytics, "record_value",
        lambda guild_id, feature, outcome="ok": recorded.append(outcome),
    )
    mock_cache.return_value = _welcome(channel, "custom_blur", "https://example.com/gone.png")

    await welcome_messages.send_welcome_message(_newcomer(guild, 7007))

    channel.assert_message_sent()
    assert recorded == ["plain_background"], "the delivery says it was degraded"


def test_a_download_from_a_host_that_never_answers_gives_up(monkeypatch):
    from app.constants import DBConfigs
    from app.services import images

    monkeypatch.setattr(DBConfigs, "IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 0.2)
    with silent_server() as port:
        started = time.monotonic()
        with pytest.raises(requests.RequestException):
            images.download_image(f"http://127.0.0.1:{port}/avatar.png")
        assert time.monotonic() - started < 2


def test_the_downloaded_image_cache_is_bounded(monkeypatch, web):
    from app.constants import DBConfigs
    from app.services import images

    for index in range(DBConfigs.IMAGE_CACHE_SIZE + 5):
        images.download_image(f"https://cdn.discordapp.com/avatars/{index}/bounded.png")

    assert images.download_image.cache_info().currsize <= DBConfigs.IMAGE_CACHE_SIZE

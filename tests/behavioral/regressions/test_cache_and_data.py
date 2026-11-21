"""Keiko's caches and data layer: a Redis that does not answer, a pool of thread
reads filled up, members found with no cache, and picture downloads bounded before
they cost memory.
"""
import ast
import asyncio
import os
import struct
import threading
import time
import warnings
import zlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import discord
import pytest
import requests
import yaml
from PIL import Image, ImageFile, UnidentifiedImageError
from redis.exceptions import ConnectionError as RedisConnectionError

import app.data as data_layer
from app.commands.block_links.service import check_message
from app.commands.welcome import service as welcome_messages
from app.constants import Commands, DBConfigs, Dependencies
from app.core import images, threads
from app.data import wait
from app.forms.discord import callbacks
from tests.behavioral.flows import birthdays
from tests.behavioral.harness import locators
from tests.generators import moderations
from tests.mocks import create_message
from tests.mocks.discord import MockInteraction, create_guild, create_member
from tests.mocks.web import FakeWeb, png

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("cache_and_data"),
]


def interaction_in(guild):
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.client = SimpleNamespace(app_commands=[])
    return interaction


@pytest.fixture
def opened(monkeypatch):
    seen = []

    async def open_feature(interaction, command_key, source, **opening):
        seen.append(command_key)
        await interaction.response.send_message(command_key)

    monkeypatch.setattr(callbacks, "open_feature", open_feature)
    return seen


async def test_a_feature_button_opens_before_a_redis_that_does_not_answer(
    deps, monkeypatch
):
    """A feature button opens its feature whether or not Redis answers its counter.

    Broke as: `run_feature_command` counted the click (`increment_redis_key`, one
    Redis call in a thread) before `open_feature`, with nothing around it. During a
    Redis outage every feature button waited for the socket timeout, up to five
    seconds, past Discord's three-second answer window, and then raised, so the
    feature never opened.

    Shared behaviour: `run_feature_command` (app/forms/discord/callbacks.py), the single
    entry from any button into a feature: the /setup cards, the greeting, /help and
    the activation panels.

    Guaranteed: the feature opens before the counter is answered, a counter that
    fails never fails the click, and the click is still counted, as the
    `command.invoked` event the analytics writer counts.
    """
    from app import forms as settings

    happened = []
    feature_open = threading.Event()

    def timing_out(key, amount=1):
        feature_open.wait(timeout=1)
        happened.append("count timed out")
        raise RedisConnectionError("Timeout reading from redis")

    monkeypatch.setattr(deps.redis_client, "incrby", timing_out)
    interaction = interaction_in(create_guild())

    async def open_feature(interaction, command_key, source, **opening):
        happened.append("feature opened")
        feature_open.set()
        await interaction.response.send_message(command_key)

    monkeypatch.setattr(callbacks, "open_feature", open_feature)

    await settings.run_feature_command(interaction, "block_links", "setup_dashboard")

    assert happened[0] == "feature opened", (
        f"the button waited on Redis before opening the feature: {happened}"
    )
    interaction.assert_response_sent("send_message")


async def test_a_feature_button_still_counts_the_click(deps, opened, analytics_events):
    from app import forms as settings

    await settings.run_feature_command(
        interaction_in(create_guild()), "block_links", "setup_dashboard"
    )

    assert opened == ["block_links"]
    assert [
        event["props"]["command"]
        for event in analytics_events
        if event["type"] == "command.invoked"
    ] == ["block_links"]


def run(coroutine):
    """`asyncio.run`, on a loop of its own that leaves the suite's loop alone."""
    with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
        return runner.run(coroutine)


@pytest.fixture
def forget_the_pool(monkeypatch):
    """Each test installs its own pool; none is left for a later test."""
    monkeypatch.setattr(threads, "_POOL", None)


def opening_a_connection_first(monkeypatch, deps):
    """Every settings read resolves the server's host name first, as pymongo does for
    each new connection."""
    find = deps.data.guild_cogs.find_cog_by_guild_id

    async def find_cog_by_guild_id(guild_id, key):
        await asyncio.get_running_loop().getaddrinfo("localhost", 27017)
        return await find(guild_id, key)

    monkeypatch.setattr(deps.data.guild_cogs, "find_cog_by_guild_id", find_cog_by_guild_id)


@pytest.mark.usefixtures("forget_the_pool")
def test_a_burst_of_messages_that_fills_the_pool_still_deletes_every_blocked_link(
    deps, mongodb, channel, member, monkeypatch
):
    """A thread job that reads Mongo while every pool thread does the same still gets its
    answer, and a read that can never be answered gives its thread back.

    Broke as: the bot's pool of `Dependencies.BLOCKING_IO_THREADS` threads was the loop's
    default executor, which also runs `loop.getaddrinfo` and the TLS handshake of every
    new Mongo connection. A thread job reading Mongo blocks its thread in `app.data.wait`
    until the loop answers; with every pool thread waiting there on reads that needed a new
    connection, the host name lookup those reads waited for queued behind them for good,
    and every message check, settings read and notifier of the bot hung with them.

    Shared behaviour: the thread seam (`app.core.threads.to_thread`) and `app.data.wait`.
    Consumer that exposed it: block links reads its settings on a thread for every message.
    Guaranteed: blocked links are still deleted when a burst of messages fills the pool
    while Mongo opens connections, and `wait` raises within `DBConfigs.MONGO_THREAD_WAIT_SECONDS`
    instead of holding its thread forever.
    """
    guild_ids = [str(9000 + index) for index in range(Dependencies.BLOCKING_IO_THREADS)]
    for guild_id in guild_ids:
        moderations.seed_cog(mongodb, "block_links", {
            "guild_id": guild_id,
            "enabled": True,
            "mode": "block_all",
            "allowed_roles": {"style": "role", "values": []},
            "allowed_chats": {"style": "channel", "values": []},
            "allowed_links": {"style": "bullet", "values": []},
            "custom_links": {"style": "composition", "values": []},
            "answer": "",
        })
    opening_a_connection_first(monkeypatch, deps)
    messages = [
        create_message(channel, member, "look https://spam-site.com") for _ in guild_ids
    ]

    async def main():
        threads.install()
        data_layer.bind_loop(asyncio.get_running_loop())
        checks = [
            check_message(guild_id, message)
            for guild_id, message in zip(guild_ids, messages)
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*checks), timeout=5)
        except TimeoutError:
            return False
        return True

    finished = run(main())

    assert finished, "the message checks never finished: the pool deadlocked"
    assert [message._delete.await_count for message in messages] == [1] * len(messages)


def test_a_read_the_loop_never_answers_gives_its_thread_back(monkeypatch):
    monkeypatch.setattr(DBConfigs, "MONGO_THREAD_WAIT_SECONDS", 0.2)
    loop = asyncio.new_event_loop()
    serving = threading.Thread(target=loop.run_forever, daemon=True)
    serving.start()
    data_layer.bind_loop(loop)
    never = asyncio.Event()

    async def unanswered():
        await never.wait()

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="Mongo"):
            wait(unanswered())
        elapsed = time.monotonic() - started
        cancelled = asyncio.run_coroutine_threadsafe(_pending(), loop).result(2)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        serving.join(2)
        loop.close()

    assert elapsed < 2
    assert cancelled == 0, "the read the thread gave up on still runs on the loop"


async def _pending():
    await asyncio.sleep(0.05)
    current = asyncio.current_task()
    return len([task for task in asyncio.all_tasks() if task is not current])


def test_no_module_of_the_app_hands_a_call_to_the_loops_own_threads():
    """`asyncio.to_thread` and `run_in_executor(None, ...)` run on the loop's own small
    pool, where a call that waits on the loop starves the lookups it waits for."""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "app")
    found = []
    for folder, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".py") or name == "threads.py":
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            if "asyncio.to_thread" in source or "run_in_executor(" in source:
                found.append(os.path.relpath(path, root))

    assert found == [], f"hand the call to app.core.threads.to_thread instead: {found}"


def pictures(scenario):
    """Every picture the screen in front of the admin draws."""
    found = []
    for item in locators.walk_items(scenario.current_message.view):
        if isinstance(item, discord.ui.MediaGallery):
            found += [str(entry.media.url) for entry in item.items]
        elif isinstance(item, discord.ui.Thumbnail):
            found.append(str(item.media.url))
    return found


async def test_a_birthday_searched_and_edited_shows_the_members_picture(
    scenario_factory, deps, monkeypatch
):
    """A member's picture is drawn whether or not any cache still holds the member.

    Broke as: once no server's members were downloaded at start, the pictures of
    the birthday cards were read from discord.py's cache and the members seen in
    the last minutes only. An admin who searched a birthday, went for a coffee and
    then pressed Edit got the member card without the member's picture, and the
    search screen lost it on the way back. Shared behaviour: `MemberPictures` and
    the context the adapter builds for every screen (`Runtime._context`).
    Consumer that exposed it: reminders_birthday, the only form with a pictured
    member select today. Guaranteed: every member a session's screens picture
    (the card's member, the item a search found, a member just chosen) is found
    before the screen is drawn, from the API when no cache has it.

    The gateway cache is empty and the recent members keep nobody: each
    screen finds the member it pictures before it is drawn.
    """
    monkeypatch.setattr(Commands, "MEMBERS_RECENT_SECONDS", 0)
    await birthdays.seed_config(deps, members=("777",))
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga", cached=False)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        birthdays.FORM
    )

    await scenario.click("search")
    await scenario.select_option(friend)
    assert friend.display_avatar.url in pictures(scenario), "the item the search found"

    await scenario.click("edit:reminders_birthday$0")
    assert friend.display_avatar.url in pictures(scenario), "the card of the edit"
    await scenario.finish()


REAL_DRAW_BANNER = welcome_messages.draw_banner


ROOT = Path(__file__).resolve().parents[3]


def _png_of(width, height):
    """A real 1-bit PNG of `width` x `height`: small to send, huge once decoded."""

    def chunk(kind, data):
        checksum = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + checksum

    row = b"\x00" * (1 + (width + 7) // 8)
    header = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height, 9))
        + chunk(b"IEND", b"")
    )


def _jpeg_of(width, height):
    """A plain grey JPEG of `width` x `height`, the way a phone camera saves one."""
    buffer = BytesIO()
    Image.new("L", (width, height), 128).save(buffer, format="JPEG")
    return buffer.getvalue()


def _saved(format_name, frames=1):
    """A small picture saved as `format_name`, with `frames` frames."""
    pictures = [
        Image.new("RGB", (64, 64), (shade * 60, 0, 0)) for shade in range(frames)
    ]
    buffer = BytesIO()
    pictures[0].save(
        buffer, format=format_name, save_all=frames > 1, append_images=pictures[1:]
    )
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def nothing_cached():
    images.download_image.cache_clear()
    yield
    images.download_image.cache_clear()


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb()
    monkeypatch.setattr(requests, "get", fake.get)
    return fake


@pytest.fixture
def bounded_pillow(monkeypatch):
    """Pillow as the bot sets it at startup."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)
    with warnings.catch_warnings():
        images.refuse_decompression_bombs()
        yield


@pytest.fixture
def decoding(monkeypatch):
    """The pictures Pillow was asked to decode, refused so that nothing is allocated."""
    asked = []

    def load(image):
        asked.append(image.size)
        width, height = image.size
        raise AssertionError(f"Pillow was asked to decode {width}x{height} pixels")

    monkeypatch.setattr(ImageFile.ImageFile, "load", load)
    return asked


def test_a_picture_too_large_to_decode_is_refused_before_pillow_decodes_it(
    web, decoding
):
    """Picture downloads are bounded before they cost memory.

    What broke (roadmap #18):

    - `download_image` read the whole body and decoded the whole picture before
      making its thumbnail, and checked neither what it was nor how big: a 500 KB
      PNG of 13,000 x 13,000 took about 680 MB;
    - avatars were asked at 1024 px for a circle the banner draws at 200, and an
      lru_cache of 128 kept decoded pictures of up to 1024 x 1024, about 512 MB.

    Shared behaviour: `app/core/images.py`, which downloads every picture a
    banner draws, and the Pillow cap set once at startup. Consumers: every member
    joining a server with welcome messages, the design gallery and the welcome
    preview. Guaranteed: a picture is refused by its headers or its dimensions
    before its body is held or decoded, a body stops being read at the cap, and an
    avatar is asked at the size the banner draws it.
    """
    url = "https://cdn.discordapp.com/attachments/1/2/huge.png"
    web.serve(url, _png_of(13_000, 13_000))

    with pytest.raises(ValueError) as refused:
        images.download_image(url)

    assert decoding == [], "the picture was decoded before its size was read"
    assert "13000x13000" in str(refused.value)


def test_a_body_longer_than_the_cap_stops_being_read_at_the_cap(web, monkeypatch):
    monkeypatch.setattr(DBConfigs, "IMAGE_MAX_BYTES", 256 * 1024)
    url = "https://cdn.discordapp.com/attachments/1/2/endless.png"
    web.serve(url, png() + bytes(8 * 1024 * 1024), chunked=True)

    with pytest.raises(images.ImageRefused):
        images.download_image(url)

    cap = DBConfigs.IMAGE_MAX_BYTES + DBConfigs.IMAGE_DOWNLOAD_CHUNK_BYTES
    assert web.read[url] <= cap, f"read {web.read[url]} bytes of a refused download"


def test_a_declared_length_over_the_cap_is_refused_before_reading(web):
    url = "https://cdn.discordapp.com/attachments/1/2/declared.png"
    web.serve(url, png(), declared_length=DBConfigs.IMAGE_MAX_BYTES + 1)

    with pytest.raises(images.ImageRefused):
        images.download_image(url)

    assert web.read.get(url, 0) == 0


def test_a_page_that_is_not_a_picture_is_refused_before_reading(web):
    url = "https://cdn.discordapp.com/attachments/1/2/page.png"
    web.serve(url, b"<html>gone</html>", content_type="text/html; charset=utf-8")

    with pytest.raises(images.ImageRefused):
        images.download_image(url)

    assert web.read.get(url, 0) == 0


async def test_a_banner_asks_for_the_avatar_at_the_size_it_draws(
    web, guild, monkeypatch
):
    monkeypatch.setattr(welcome_messages, "draw_banner", REAL_DRAW_BANNER)
    member = create_member(guild, id=7201, name="NewUser")

    await welcome_messages.draw_banner(None, "welcome", member)

    [avatar] = web.requested
    assert urlparse(avatar).path == "/avatars/7201/fake.png"
    assert parse_qs(urlparse(avatar).query)["size"] == [str(Commands.WELCOME_AVATAR_SIZE)]
    assert Commands.WELCOME_AVATAR_SIZE >= min(Commands.WELCOME_BANNER_SIZE) // 2, (
        "the banner draws the avatar on a circle half its height wide"
    )


def test_once_bounded_pillow_opens_nothing_larger_than_the_cap(monkeypatch):
    side = int(DBConfigs.IMAGE_MAX_OPEN_PIXELS**0.5)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)

    with warnings.catch_warnings():
        images.refuse_decompression_bombs()

        with pytest.raises(Image.DecompressionBombWarning):
            Image.open(BytesIO(_png_of(side + 1, side)))
        assert Image.open(BytesIO(_png_of(side, side))).size == (side, side)


def test_the_cap_is_set_before_the_bot_starts():
    entry = ast.parse((Path(__file__).parents[3] / "__main__.py").read_text())
    calls = [
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in sorted(
            (node for node in ast.walk(entry) if isinstance(node, ast.Call)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        if isinstance(node.func, (ast.Attribute, ast.Name))
    ]

    assert "refuse_decompression_bombs" in calls
    assert calls.index("refuse_decompression_bombs") < calls.index("create_app")


def test_a_large_phone_photo_is_decoded_smaller_while_a_png_as_large_is_refused(
    web, bounded_pillow
):
    """A 48 MP photo from a phone is an ordinary custom background, and Pillow can
    decode a JPEG at a fraction of its size; a PNG cannot be, so the same size is
    refused before it is decoded."""
    photo = "https://cdn.discordapp.com/attachments/1/2/phone.jpg"
    poster = "https://cdn.discordapp.com/attachments/1/2/poster.png"
    web.serve(photo, _jpeg_of(8000, 6000), content_type="image/jpeg")
    web.serve(poster, _png_of(8000, 6000))

    assert max(images.download_image(photo).size) <= max(DBConfigs.IMAGE_MAX_SIZE)
    with pytest.raises(images.ImageRefused):
        images.download_image(poster)


def test_the_image_cache_fits_in_a_tenth_of_the_container_memory():
    """Each cached picture is at most `IMAGE_MAX_SIZE` at 4 bytes a pixel (RGBA, the
    widest mode a banner reads), so the whole cache has a ceiling to compare with
    the deploy's memory cap."""
    deploy = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    limit = str(deploy["jobs"]["deploy"]["env"]["MEMORY_LIMIT"]).lower()
    container = int(limit[:-1]) * {"m": 1024**2, "g": 1024**3}[limit[-1]]
    width, height = DBConfigs.IMAGE_MAX_SIZE

    cache = DBConfigs.IMAGE_CACHE_SIZE * width * height * 4

    assert cache <= container / 10, f"{cache} bytes of pictures under {limit}"


def test_a_picture_in_a_format_no_banner_needs_is_refused(web):
    """Avatars, icons and the pictures admins send are PNG, JPEG, GIF or WEBP;
    every other decoder Pillow ships is surface a download never needs."""
    url = "https://cdn.discordapp.com/attachments/1/2/old.bmp"
    web.serve(url, _saved("BMP"), content_type="image/bmp")

    with pytest.raises(UnidentifiedImageError):
        images.download_image(url)


@pytest.mark.parametrize("format_name, frames", [("PNG", 1), ("GIF", 3)])
def test_a_cached_picture_keeps_none_of_the_file_it_came_from(web, format_name, frames):
    """Pillow keeps the whole downloaded file in `_fp` to read the other frames;
    the cache holds pictures, so it must not hold their files too."""
    url = f"https://cdn.discordapp.com/attachments/1/2/cached.{format_name.lower()}"
    web.serve(
        url, _saved(format_name, frames), content_type=f"image/{format_name.lower()}"
    )

    image = images.download_image(url)

    assert getattr(image, "_fp", None) is None and getattr(image, "fp", None) is None


@pytest.mark.parametrize("frames", [1, 3], ids=["still", "animated"])
async def test_a_webp_an_admin_sends_is_drawn_on_the_welcome_banner(
    web, guild, monkeypatch, frames
):
    """The refused-picture error offers WEBP beside PNG, JPG and GIF, so a WEBP must
    reach the banner: decoded by the same `download_image` and painted by the same
    `draw_banner` as the others, a still one and an animated one (its first frame)."""
    monkeypatch.setattr(welcome_messages, "draw_banner", REAL_DRAW_BANNER)
    url = "https://cdn.discordapp.com/attachments/1/2/banner.webp"
    pictures = [Image.new("RGB", (640, 320), (0, 0, 255)) for _ in range(frames)]
    buffer = BytesIO()
    pictures[0].save(
        buffer, format="WEBP", save_all=frames > 1, append_images=pictures[1:]
    )
    web.serve(url, buffer.getvalue(), content_type="image/webp")
    member = create_member(guild, id=7202, name="NewUser")

    banner = Image.open(await welcome_messages.draw_banner(url, "welcome", member))

    red, green, blue, _alpha = banner.convert("RGBA").getpixel((5, banner.height - 5))
    assert blue > 200 and red < 40 and green < 40, "the banner is drawn over the WEBP"


def test_a_birthday_picture_reaches_discord_as_its_url(guild):
    """Keiko never decodes a birthday picture: the celebration hands Discord the
    url the member card saved, and Discord draws a WEBP like any other."""
    from app.commands.birthdays.service import build_celebration_embed

    url = "https://cdn.discordapp.com/attachments/1/2/party.webp"
    member = create_member(guild, id=7203, name="Aniversariante")
    item = {"date": "05-12", "image": {"mode": "custom", "url": url}}

    embed = build_celebration_embed(item, member, guild, {}, "pt-br")

    assert embed.image.url == url

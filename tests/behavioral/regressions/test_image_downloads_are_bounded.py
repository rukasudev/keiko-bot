"""Picture downloads are bounded before they cost memory.

What broke (roadmap #18):

- `download_image` read the whole body and decoded the whole picture before
  making its thumbnail, and checked neither what it was nor how big: a 500 KB
  PNG of 13,000 x 13,000 took about 680 MB;
- avatars were asked at 1024 px for a circle the banner draws at 200, and an
  lru_cache of 128 kept decoded pictures of up to 1024 x 1024, about 512 MB.

Shared behaviour: `app/services/images.py`, which downloads every picture a
banner draws, and the Pillow cap set once at startup. Consumers: every member
joining a server with welcome messages, the design gallery and the welcome
preview. Guaranteed: a picture is refused by its headers or its dimensions
before its body is held or decoded, a body stops being read at the cap, and an
avatar is asked at the size the banner draws it.
"""

import ast
import struct
import warnings
import zlib
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import yaml
from PIL import Image, ImageFile, UnidentifiedImageError

from app.constants import DBConfigs, WelcomeDesign
from app.services import images, welcome_messages
from tests.mocks import create_member
from tests.mocks.web import FakeWeb, png

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("images")]

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
    assert parse_qs(urlparse(avatar).query)["size"] == [str(WelcomeDesign.AVATAR_SIZE)]
    assert WelcomeDesign.AVATAR_SIZE >= min(WelcomeDesign.BANNER_SIZE) // 2, (
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

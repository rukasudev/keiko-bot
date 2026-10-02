"""Pictures downloaded over HTTP without freezing the bot or filling its memory."""

import asyncio
import warnings
from functools import lru_cache
from io import BytesIO
from typing import Dict

import requests
from PIL import Image

from app.constants import DBConfigs
from app.services import cdn

_downloads: Dict[str, "asyncio.Task[Image.Image]"] = {}


class ImageRefused(ValueError):
    """A download that is not a picture, or a picture too big to hold or decode."""


def refuse_decompression_bombs() -> None:
    """Refuse, process-wide, to open a picture over `IMAGE_MAX_OPEN_PIXELS`."""
    Image.MAX_IMAGE_PIXELS = DBConfigs.IMAGE_MAX_OPEN_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)


@lru_cache(maxsize=DBConfigs.IMAGE_CACHE_SIZE)
def download_image(url: str) -> Image.Image:
    """The picture at `url`, reduced to what a screen can show, kept among the recent ones."""
    image = Image.open(BytesIO(_download(url)), formats=DBConfigs.IMAGE_FORMATS)
    image.draft(None, DBConfigs.IMAGE_MAX_SIZE)
    width, height = image.size
    if width * height > DBConfigs.IMAGE_MAX_PIXELS:
        raise ImageRefused(f"{width}x{height} is more pixels than a banner decodes")

    image.thumbnail(DBConfigs.IMAGE_MAX_SIZE)
    image.load()
    return image.copy()


def _download(url: str) -> bytes:
    """The body at `url`, refused by its headers or at the byte cap."""
    with requests.get(
        url, timeout=DBConfigs.IMAGE_DOWNLOAD_TIMEOUT_SECONDS, stream=True
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if not content_type.lower().startswith("image/"):
            raise ImageRefused(f"{content_type or 'no content type'} is not a picture")

        declared = int(response.headers.get("Content-Length") or 0)
        if declared > DBConfigs.IMAGE_MAX_BYTES:
            raise ImageRefused(f"{declared} bytes is more than a picture may weigh")

        body = bytearray()
        for chunk in response.iter_content(DBConfigs.IMAGE_DOWNLOAD_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > DBConfigs.IMAGE_MAX_BYTES:
                raise ImageRefused(f"more than {DBConfigs.IMAGE_MAX_BYTES} bytes")

        return bytes(body)


async def fetch_image(url: str) -> Image.Image:
    """The picture at `url`, downloaded once however many screens ask for it at the same time."""
    running = _downloads.get(url)
    if running is None:
        running = asyncio.ensure_future(_fetch(url))
        _downloads[url] = running
        running.add_done_callback(lambda _finished: _downloads.pop(url, None))

    return await asyncio.shield(running)


async def _fetch(url: str) -> Image.Image:
    try:
        return await asyncio.to_thread(download_image, url)
    except requests.HTTPError:
        if not cdn.is_attachment(url):
            raise
        refreshed = await cdn.refresh_attachment_url(url)
        if refreshed == url:
            raise
        return await asyncio.to_thread(download_image, refreshed)

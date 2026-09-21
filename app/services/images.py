"""Pictures downloaded over HTTP without freezing the bot or filling its memory."""

import asyncio
from functools import lru_cache
from io import BytesIO
from typing import Dict

import requests
from PIL import Image

from app.constants import DBConfigs
from app.services import cdn

_downloads: Dict[str, "asyncio.Task[Image.Image]"] = {}


@lru_cache(maxsize=DBConfigs.IMAGE_CACHE_SIZE)
def download_image(url: str) -> Image.Image:
    """The picture at `url`, reduced to what a screen can show, kept among the recent ones."""
    response = requests.get(url, timeout=DBConfigs.IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()
    image = Image.open(BytesIO(response.content))
    image.load()
    image.thumbnail(DBConfigs.IMAGE_MAX_SIZE)
    return image


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

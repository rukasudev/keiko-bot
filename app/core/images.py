"""The pictures Keiko shows: downloaded over HTTP without freezing the bot or filling its
memory, and Keiko's own files kept on Discord's CDN."""

import asyncio
import time
import warnings
from functools import lru_cache
from io import BytesIO
from typing import Dict, Optional, Tuple, Union
from urllib.parse import urlparse

import discord
import requests
from discord.http import Route
from PIL import Image

import app
from app import logger
from app.constants import DBConfigs, DiscordLimits
from app.constants import LogTypes as logconstants

_downloads: Dict[str, "asyncio.Task[Image.Image]"] = {}
_uploaded_assets: Dict[str, Tuple[str, float]] = {}


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
        if not is_attachment(url):
            raise
        refreshed = await refresh_attachment_url(url)
        if refreshed == url:
            raise
        return await asyncio.to_thread(download_image, refreshed)


def is_attachment(url: str) -> bool:
    """Whether `url` is a signed Discord attachment link."""
    parsed = urlparse(url)
    return parsed.hostname in ("cdn.discordapp.com", "media.discordapp.net") and (
        parsed.path.startswith("/attachments/")
    )


async def upload(data: Union[BytesIO, str], filename: Optional[str] = None) -> str:
    """Send a file to the dump channel and return its CDN url."""
    channel = app.bot.get_channel(app.bot.config.ADMIN_DUMP_CHANNEL_ID)
    message = await channel.send(file=discord.File(data, filename=filename))
    return str(message.attachments[0].url)


async def upload_asset(path: str) -> str:
    """The CDN url of a file from `app/assets`, uploaded once and re-signed after that."""
    url, signed_at = _uploaded_assets.get(path, ("", 0.0))
    if url and time.monotonic() - signed_at < DiscordLimits.ATTACHMENT_URL_REFRESH_SECONDS:
        return url

    if url:
        refreshed = await refresh_attachment_url(url)
        if refreshed != url:
            _uploaded_assets[path] = (refreshed, time.monotonic())
            return refreshed

    url = await upload(path)
    _uploaded_assets[path] = (url, time.monotonic())
    return url


async def refresh_attachment_url(url: str) -> str:
    """A freshly signed copy of an attachment link, or the link itself when refused."""
    try:
        data = await app.bot.http.request(
            Route("POST", "/attachments/refresh-urls"),
            json={"attachment_urls": [url]},
        )
        return str(data["refreshed_urls"][0]["refreshed"])
    except (discord.HTTPException, KeyError, IndexError, TypeError) as error:
        logger.warn(
            f"Could not refresh an attachment link: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
        return url

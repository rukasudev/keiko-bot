"""Keiko's files on Discord's CDN: uploads, reused assets and expired links."""

import time
from io import BytesIO
from typing import Dict, Optional, Tuple, Union
from urllib.parse import urlparse

import discord
from discord.http import Route

import app
from app import logger
from app.constants import DiscordLimits
from app.constants import LogTypes as logconstants

_uploaded_assets: Dict[str, Tuple[str, float]] = {}


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

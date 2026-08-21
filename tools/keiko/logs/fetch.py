"""Reads the Discord logs channel over REST. No gateway, no bot.

Instantiating `DiscordBot` to read message history would run `setup_hook`, load
every cog and open a gateway session with the production token — a large side
effect for a read. The REST endpoint needs none of that.

The channel holds one message per day, so a full backfill of several years is
roughly a dozen requests.
"""
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

API = "https://discord.com/api/v10"
PAGE = 100
MAX_ATTEMPTS = 5


class FetchError(RuntimeError):
    pass


def token() -> str:
    value = os.getenv("DISCORD_BOT_TOKEN_PROD") or os.getenv("DISCORD_BOT_TOKEN")
    if not value:
        raise FetchError(
            "Set DISCORD_BOT_TOKEN_PROD (a bot in the admin guild) before syncing."
        )
    return value


def channel_id() -> str:
    value = os.getenv("ADMIN_LOGS_FILES_CHANNEL_ID")
    if not value:
        raise FetchError("Set ADMIN_LOGS_FILES_CHANNEL_ID to the log files channel.")
    return value


def _request(url: str, headers: Dict[str, str]) -> bytes:
    """Honours Retry-After instead of hammering a rate limit."""
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < MAX_ATTEMPTS - 1:
                time.sleep(float(error.headers.get("Retry-After", 2)) + 0.5)
                continue
            if error.code in (401, 403):
                raise FetchError(
                    f"Discord refused the request ({error.code}). "
                    "Is the token in the admin guild and able to read that channel?"
                ) from error
            raise FetchError(f"Discord returned {error.code} for {url}") from error
        except urllib.error.URLError as error:
            raise FetchError(f"Could not reach Discord: {error.reason}") from error

    raise FetchError("Gave up after repeated rate limits.")


def iter_messages(channel: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Walks the channel backwards, newest first, one page at a time."""
    channel = channel or channel_id()
    headers = {
        "Authorization": f"Bot {token()}",
        "User-Agent": "KeikoLogTool (internal, +https://github.com/rukasudev/keiko-bot)",
    }

    import json

    before = None
    while True:
        url = f"{API}/channels/{channel}/messages?limit={PAGE}"
        if before:
            url += f"&before={before}"

        page: List[Dict[str, Any]] = json.loads(_request(url, headers))
        if not page:
            return

        for message in page:
            yield message

        before = page[-1]["id"]
        if len(page) < PAGE:
            return


def iter_log_attachments(channel: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Every attachment that looks like a Keiko log file, newest first."""
    for message in iter_messages(channel):
        for attachment in message.get("attachments") or []:
            name = attachment.get("filename", "")
            if name.endswith(".log") or name.endswith(".jsonl.gz"):
                yield {
                    "filename": name,
                    "url": attachment["url"],
                    "size": attachment.get("size", 0),
                    "message_id": message["id"],
                }


def download(url: str) -> bytes:
    return _request(url, {"User-Agent": "KeikoLogTool (internal)"})

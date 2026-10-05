"""The daily consolidation that turns the hot window into the archive.

Mongo keeps 30 days (`DEBUG_LOGS_TTL_SECONDS`); everything older lives as a
file on the Discord logs channel, which is already free, for 90 days
(`DAILY_LOGS_RETENTION_DAYS`, deleted by `logs_files`). Once a day this
reads the previous day out of Mongo and posts it there as gzipped JSON Lines,
one record per line.

It reads Mongo rather than the rotated `.log`, and that is the point: the text
file lives inside the container with no volume, so a deploy takes that day's
file with it. The export has no such hole, and it keeps the full traceback that
the Discord embeds have to truncate.
"""
import gzip
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord

from app.data import logs as logs_data
from app.services.utils import ml

FILENAME = "keiko_logs_{date}.jsonl.gz"
ADMIN_LOCALE = "en-us"


def previous_day(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=1)


def serialize(document: Dict[str, Any]) -> str:
    """Line-oriented and tool-friendly: no `_id`, timestamps as ISO-8601."""
    record = {key: value for key, value in document.items() if key != "_id"}
    timestamp = record.get("ts")
    if isinstance(timestamp, datetime):
        record["ts"] = timestamp.isoformat()
    return json.dumps(record, ensure_ascii=False, default=str)


def build_archive(day: datetime) -> Tuple[Optional[bytes], int]:
    """Stream one day into a gzip buffer. Returns (payload, record count)."""
    return gzip_lines(serialize(document) for document in logs_data.iter_logs_for_day(day))


def gzip_lines(lines: Iterable[str]) -> Tuple[Optional[bytes], int]:
    """Stream lines into one gzip buffer. Returns (payload, line count), (None, 0) for none."""
    buffer = io.BytesIO()
    written = 0

    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as stream:
        for line in lines:
            stream.write((line + "\n").encode("utf-8"))
            written += 1

    if not written:
        return None, 0

    return buffer.getvalue(), written


def part_names(stem: str, extension: str, total: int) -> List[str]:
    """The names of a file sent in `total` parts: `stem.ext`, or `stem_1-of-3.ext` and on."""
    if total == 1:
        return [f"{stem}{extension}"]
    return [f"{stem}_{index}-of-{total}{extension}" for index in range(1, total + 1)]


def build_file(filename: str, payload: bytes) -> discord.File:
    """A payload as the attachment a channel receives."""
    return discord.File(io.BytesIO(payload), filename=filename)


async def send_files(channel: Any, content: str, files: List[Tuple[str, bytes]]) -> None:
    """`content` with the first file, then every other file in a message of its own."""
    attachments = [build_file(filename, payload) for filename, payload in files]
    await channel.send(content, file=attachments[0] if attachments else None)

    for attachment in attachments[1:]:
        await channel.send(file=attachment)


def build_daily_file(day: datetime) -> Tuple[Optional[discord.File], int]:
    payload, written = build_archive(day)
    if not payload:
        return None, 0

    filename = FILENAME.format(date=day.strftime("%Y-%m-%d"))
    return build_file(filename, payload), written


def build_message(day: datetime, written: int, locale: str = ADMIN_LOCALE) -> str:
    """The admin channel has no user, so the bot's own locale is the one used."""
    return ml("messages.admin-logs.daily-export", locale).format(
        date=day.strftime("%Y-%m-%d"), count=written
    )

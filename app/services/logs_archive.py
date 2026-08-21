"""The daily consolidation that turns the hot window into the archive.

Mongo keeps 30 days (`DEBUG_LOGS_TTL_SECONDS`); everything older lives as a
file on the Discord logs channel, which is already free, already permanent, and
already holds years of history. Once a day this reads the previous day out of
Mongo and posts it there as gzipped JSON Lines, one record per line.

It reads Mongo rather than the rotated `.log`, and that is the point: the text
file lives inside the container with no volume, so a deploy takes that day's
file with it. The export has no such hole, and it keeps the full traceback that
the Discord embeds have to truncate.
"""
import gzip
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

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
    buffer = io.BytesIO()
    written = 0

    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as stream:
        for document in logs_data.iter_logs_for_day(day):
            stream.write((serialize(document) + "\n").encode("utf-8"))
            written += 1

    if not written:
        return None, 0

    return buffer.getvalue(), written


def build_daily_file(day: datetime) -> Tuple[Optional[discord.File], int]:
    payload, written = build_archive(day)
    if not payload:
        return None, 0

    filename = FILENAME.format(date=day.strftime("%Y-%m-%d"))
    return discord.File(io.BytesIO(payload), filename=filename), written


def build_message(day: datetime, written: int, locale: str = ADMIN_LOCALE) -> str:
    """The admin channel has no user, so the bot's own locale is the one used."""
    return ml("messages.admin-logs.daily-export", locale).format(
        date=day.strftime("%Y-%m-%d"), count=written
    )

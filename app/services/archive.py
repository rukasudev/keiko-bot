"""What analytics keeps past its 90 days: each month's raw events, with no server or
member in them, as a sealed file on the logs channel, and the current size of every
guild that has a profile."""
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import blake2b
from typing import Any, Dict, List, Mapping, Optional, Tuple

from bson import json_util
from pyrage import x25519

from app.constants import Commands as constants
from app.data import analytics as analytics_data
from app.data import archive as archive_data
from app.services import sealing
from app.services.analytics import bucket_size
from app.services.logs_archive import ADMIN_LOCALE, gzip_lines, part_names
from app.services.utils import ml


@dataclass(frozen=True)
class ArchiveFile:
    """One sealed file of a month's archive: its name, its bytes and how many events."""

    filename: str
    payload: bytes
    count: int


def last_month(now: datetime) -> datetime:
    """The first moment, in UTC, of the month before the one `now` is in."""
    this_month = now.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return (this_month - timedelta(days=1)).replace(day=1)


def is_archived(month: datetime) -> bool:
    """Whether the month's events were already posted."""
    return archive_data.find_archived_month(month.strftime("%Y-%m")) is not None


def months_to_archive(now: datetime) -> List[datetime]:
    """Every complete month never posted whose start the events still reach, oldest first."""
    reached = now - timedelta(seconds=constants.ANALYTICS_EVENTS_TTL_SECONDS)
    months = []

    month = last_month(now)
    while month >= reached:
        months.append(month)
        month = last_month(month)

    return [month for month in reversed(months) if not is_archived(month)]


def build_month_files(
    month: datetime, limit: int, recipient: x25519.Recipient
) -> List[ArchiveFile]:
    """The month's events as gzipped Extended JSON Lines sealed to `recipient`, in halves
    of the month until every file fits in `limit` bytes; none when nothing was recorded.
    Sessions are hashed with a key drawn for this pass alone and never kept."""
    key = secrets.token_bytes(32)
    parts = _parts(month, _month_after(month), sealing.plaintext_limit(limit), key)
    names = part_names(f"keiko_events_{month:%Y-%m}", ".jsonl.gz.age", len(parts))

    return [
        ArchiveFile(name, sealing.seal(payload, recipient), count)
        for name, (payload, count) in zip(names, parts)
    ]


def build_month_message(
    month: datetime, files: List[ArchiveFile], locale: str = ADMIN_LOCALE
) -> str:
    """The month in one message: what it holds, and in how many files."""
    lines = [
        ml("messages.admin-logs.monthly-events", locale).format(
            month=month.strftime("%Y-%m"), count=sum(part.count for part in files)
        )
    ]
    if len(files) > 1:
        lines.append(ml("messages.admin-logs.files-parts", locale).format(files=len(files)))
    return "\n".join(lines)


def mark_archived(month: datetime, files: List[ArchiveFile], now: datetime) -> None:
    """Remember the month went out, so no later pass posts it again."""
    archive_data.mark_month_archived(
        month.strftime("%Y-%m"),
        {
            "posted_at": now,
            "events": sum(part.count for part in files),
            "files": len(files),
        },
    )


def record_guild_sizes(member_counts: Dict[str, Optional[int]], now: datetime) -> int:
    """The current size bucket of every guild that has a profile; never creates one."""
    operations = [
        analytics_data.build_profile_operation(
            guild_id,
            set_fields={"size_bucket": bucket_size(count), "size_measured_at": now},
            upsert=False,
        )
        for guild_id, count in member_counts.items()
        if count is not None
    ]
    analytics_data.update_profiles(operations)
    return len(operations)


def without_identity(event: Mapping[str, Any], key: bytes) -> Dict[str, Any]:
    """An event with no server, member or other Discord id: only the envelope fields the
    archive keeps, no property named after an id, and its session hashed under `key`."""
    kept = {
        field: event[field] for field in constants.ANALYTICS_ARCHIVE_FIELDS if field in event
    }

    if "props" in kept:
        kept["props"] = {
            name: value for name, value in (kept["props"] or {}).items()
            if not name.endswith("_id")
        }

    if kept.get("session_id"):
        kept["session_id"] = blake2b(
            str(kept["session_id"]).encode("utf-8"), key=key, digest_size=8
        ).hexdigest()

    return kept


def _parts(
    start: datetime, end: datetime, limit: int, key: bytes
) -> List[Tuple[bytes, int]]:
    payload, count = gzip_lines(
        json_util.dumps(
            without_identity(event, key), json_options=json_util.RELAXED_JSON_OPTIONS
        )
        for event in analytics_data.iter_events_between(start, end)
    )
    if not payload:
        return []

    middle = start + (end - start) / 2
    if len(payload) <= limit or count == 1 or middle == start:
        return [(payload, count)]
    return _parts(start, middle, limit, key) + _parts(middle, end, limit, key)


def _month_after(month: datetime) -> datetime:
    return (month + timedelta(days=32)).replace(day=1)

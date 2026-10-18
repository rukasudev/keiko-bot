"""What Keiko keeps on the logs files channel, and for how long: the daily logs, each
month's analytics events and the daily backup.

Mongo keeps logs 30 days (`DEBUG_LOGS_TTL_SECONDS`); once a day the previous day goes to
the channel as gzipped JSON Lines, one record per line. It reads Mongo rather than the
rotated `.log`, which lives inside the container with no volume, so a deploy takes that
day's file with it; the export has no such hole, and it keeps the full traceback that the
Discord embeds have to truncate.

Each complete month of events goes out once, with no server or member in it, and a backup
of Keiko's databases goes out every day. Both are sealed to one age public key, so only
the holder of the private key opens them. Right after the backup, Keiko's own files past
their kind's age are deleted: backups after 30 days, daily logs after 90
(`DAILY_LOGS_RETENTION_DAYS`), the monthly archive never.
"""
import asyncio
import gzip
import io
import json
import re
import secrets
import shutil
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import blake2b
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import discord
import pyrage
from bson import json_util
from pyrage import x25519

from app import logger
from app.constants import Commands as constants
from app.constants import DBConfigs
from app.constants import LogTypes as logconstants
from app.data import analytics as analytics_data
from app.data import logs as logs_data
from app.data import util as util_data
from app.data.indexes import INDEXES, located
from app.services.trace import as_utc
from app.services.utils import ml

FILENAME = "keiko_logs_{date}.jsonl.gz"
ADMIN_LOCALE = "en-us"


def files_channel(bot: Any, job: str) -> Any:
    """The logs files channel `job` posts to, or None, and a warning, when it is missing."""
    channel = bot.get_channel(bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)
    if not channel:
        logger.warn(
            f"{job} not posted: the logs files channel was not found",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None
    return channel


def sealing_key(bot: Any, job: str) -> Optional[x25519.Recipient]:
    """Who `job`'s files are sealed to, or None, and a warning, without a valid key."""
    recipient = age_recipient(bot.config.BACKUP_AGE_PUBLIC_KEY)
    if recipient is None:
        logger.warn(
            f"{job} not posted: /keiko/backup/age_public_key "
            "(BACKUP_AGE_PUBLIC_KEY locally) is missing or is not an age public key, "
            "and a file is never posted unencrypted",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
    return recipient


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


def age_recipient(public_key: Optional[str]) -> Optional[x25519.Recipient]:
    """Who a file is sealed to; None without a valid age public key."""
    try:
        return x25519.Recipient.from_str((public_key or "").strip())
    except pyrage.RecipientError:
        return None


def seal(payload: bytes, recipient: x25519.Recipient) -> bytes:
    """`payload` encrypted to `recipient`, so only the matching private key opens it."""
    return pyrage.encrypt(payload, [recipient])


def plaintext_limit(limit: int) -> int:
    """The largest payload whose sealed file still fits in `limit` bytes."""
    chunks = -(-limit // DBConfigs.AGE_CHUNK_BYTES)
    return limit - DBConfigs.AGE_HEADER_BYTES - DBConfigs.AGE_TAG_BYTES * chunks


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
    return analytics_data.find_archived_month(month.strftime("%Y-%m")) is not None


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
    parts = _parts(month, _month_after(month), plaintext_limit(limit), key)
    names = part_names(f"keiko_events_{month:%Y-%m}", ".jsonl.gz.age", len(parts))

    return [
        ArchiveFile(name, seal(payload, recipient), count)
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
    analytics_data.mark_month_archived(
        month.strftime("%Y-%m"),
        {
            "posted_at": now,
            "events": sum(part.count for part in files),
            "files": len(files),
        },
    )


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


@dataclass(frozen=True)
class BackupFile:
    """One sealed zip of the backup: its name, its bytes and the documents of each collection."""

    filename: str
    payload: bytes
    counts: Dict[str, int]


@dataclass(frozen=True)
class _Single:
    pair: Tuple[str, str]
    payload: bytes
    counts: Dict[str, int]


def backed_up_collections() -> List[Tuple[str, str]]:
    """Every (database, collection) the backup copies: settings first, and last the
    collections whose documents expire a fixed time after they are written."""
    expiring = {
        located(collection)
        for collection, _keys, options in INDEXES
        if options.get("expireAfterSeconds", 0) > 0
    }
    found = []

    for database, wanted in DBConfigs.BACKUP_COLLECTIONS.items():
        present = util_data.collection_names(database)
        names = present if wanted is None else [name for name in present if name in wanted]
        for collection in sorted(names):
            skipped = f"{database}.{collection}" in DBConfigs.BACKUP_SKIPPED_COLLECTIONS
            if not skipped and not collection.startswith("system."):
                found.append((database, collection))

    return sorted(found, key=lambda pair: pair in expiring)


def build_backup(
    now: datetime, limit: int, recipient: x25519.Recipient
) -> Tuple[List[BackupFile], List[str]]:
    """Sealed zips of at most `limit` bytes, packed one collection at a time in order,
    and the collections too large on their own."""
    budget = plaintext_limit(limit)
    singles: List[_Single] = []
    skipped: List[str] = []

    for pair in backed_up_collections():
        zipped = _zipped(pair, now, budget)
        if zipped is None:
            skipped.append(f"{pair[0]}.{pair[1]}")
        else:
            singles.append(_Single(pair, *zipped))

    sealed: List[Tuple[bytes, Dict[str, int]]] = []
    for pack in _packs(singles, budget):
        payload, counts = _merged(pack, now)
        encrypted = seal(payload, recipient)
        if len(encrypted) > limit:
            skipped.extend(counts)
        else:
            sealed.append((encrypted, counts))

    names = part_names(f"keiko_backup_{now:%Y-%m-%d}", ".zip.age", len(sealed))
    files = [
        BackupFile(name, payload, counts)
        for name, (payload, counts) in zip(names, sealed)
    ]
    return files, skipped


def build_summary(
    now: datetime,
    files: List[BackupFile],
    skipped: List[str],
    locale: str = ADMIN_LOCALE,
) -> str:
    """The pass in one message: what went, in how many files, and what was left out."""
    counts = {name: count for part in files for name, count in part.counts.items()}
    lines = [
        ml("messages.admin-logs.daily-backup", locale).format(
            date=now.strftime("%Y-%m-%d"),
            collections=len(counts),
            documents=sum(counts.values()),
        )
    ]

    if len(files) > 1:
        lines.append(
            ml("messages.admin-logs.files-parts", locale).format(files=len(files))
        )
    if skipped:
        names = ", ".join(f"`{name}`" for name in skipped)
        lines.append(
            ml("messages.admin-logs.daily-backup-skipped", locale).format(collections=names)
        )

    return "\n".join(lines)


def _packs(singles: List[_Single], limit: int) -> List[List[_Single]]:
    packs: List[List[_Single]] = []
    current: List[_Single] = []
    size = 0

    for single in singles:
        if current and size + len(single.payload) > limit:
            packs.append(current)
            current, size = [], 0
        current.append(single)
        size += len(single.payload)

    if current:
        packs.append(current)
    return packs


def _merged(pack: List[_Single], now: datetime) -> Tuple[bytes, Dict[str, int]]:
    counts: Dict[str, int] = {}
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for single in pack:
            name = f"{single.pair[0]}/{single.pair[1]}.jsonl"
            with zipfile.ZipFile(io.BytesIO(single.payload)) as alone:
                with alone.open(name) as source, archive.open(name, "w") as target:
                    shutil.copyfileobj(source, target)
            counts.update(single.counts)

        manifest = {"created_at": now.isoformat(), "collections": counts}
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))

    return buffer.getvalue(), counts


def _zipped(
    pair: Tuple[str, str], now: datetime, limit: int
) -> Optional[Tuple[bytes, Dict[str, int]]]:
    database, collection = pair
    written = 0
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open(f"{database}/{collection}.jsonl", "w") as stream:
            for document in util_data.iter_documents(database, collection):
                line = json_util.dumps(
                    document, json_options=json_util.CANONICAL_JSON_OPTIONS
                )
                stream.write((line + "\n").encode("utf-8"))
                written += 1
                if buffer.tell() > limit:
                    return None

        counts = {f"{database}.{collection}": written}
        manifest = {"created_at": now.isoformat(), "collections": counts}
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))

    payload = buffer.getvalue()
    return (payload, counts) if len(payload) <= limit else None


@dataclass(frozen=True)
class Kind:
    """Files Keiko posts under names that fully match `pattern`, kept `days` after posting."""

    pattern: str
    days: int


@dataclass(frozen=True)
class Cleared:
    """One pass: the files it deleted, the old files it left (the ones it failed to
    delete included), each delete error with how many files it hit, and what stopped it."""

    deleted: int
    left: int
    failures: Dict[str, int]
    stopped: Optional[str]

    def report(self) -> str:
        """The pass in one line for the log channel."""
        if self.stopped:
            line = (
                "Deleting old files from the logs files channel stopped after "
                f"{self.deleted} deleted ({self.stopped})"
            )
        else:
            line = f"Deleted {self.deleted} old file(s) from the logs files channel"

        if self.left:
            line += f", {self.left} left for the next passes"

        if self.failures:
            errors = "; ".join(f"{error} ({count})" for error, count in self.failures.items())
            line += f"; {sum(self.failures.values())} could not be deleted: {errors}"

        return line


def daily_logs() -> Kind:
    """The daily log files: the structured export and the text log the rotating handler posts."""
    return Kind(
        r"keiko_logs_\d{4}-\d{2}-\d{2}\.jsonl\.gz|keiko_log\.log",
        constants.DAILY_LOGS_RETENTION_DAYS,
    )


def is_daily_log(filename: str) -> bool:
    """Whether `filename` names one of the daily log files."""
    return re.fullmatch(daily_logs().pattern, filename) is not None


def expiring_kinds(with_backups: bool) -> List[Kind]:
    """What a pass deletes once old: the daily logs, and the backups on a day one went out."""
    kinds = [daily_logs()]

    if with_backups:
        kinds.append(
            Kind(
                r"keiko_backup_\d{4}-\d{2}-\d{2}(?:_\d+-of-\d+)?\.zip\.age",
                constants.BACKUP_RETENTION_DAYS,
            )
        )

    return kinds


def kind_of(message: Any, author_id: int, kinds: List[Kind]) -> Optional[Kind]:
    """The kind of a message `author_id` posted whose files all belong to it, else none."""
    names = [attachment.filename for attachment in message.attachments]
    if message.author.id != author_id or not names:
        return None

    return next(
        (kind for kind in kinds if all(re.fullmatch(kind.pattern, name) for name in names)),
        None,
    )


async def delete_old_files(
    channel: Any, author_id: int, now: datetime, with_backups: bool
) -> Cleared:
    """Delete, oldest first and in at most LOGS_FILES_DELETES_PER_PASS attempts, the files
    `author_id` posted on `channel` past their kind's age, walking only the history after
    the last pass that had every kind and left nothing behind; an error stops the pass,
    and the result says how far it got."""
    kinds = expiring_kinds(with_backups)
    deleted, attempts, beyond_cap = 0, 0, 0
    failures: Counter[str] = Counter()
    stopped = None

    try:
        retention = await asyncio.to_thread(logs_data.find_retention)
        async for message in channel.history(
            limit=None,
            after=as_utc(retention["cleared_until"]) if retention else None,
            before=now - timedelta(days=min(kind.days for kind in kinds)),
            oldest_first=True,
        ):
            kind = kind_of(message, author_id, kinds)
            if kind is None or message.created_at > now - timedelta(days=kind.days):
                continue

            if attempts >= constants.LOGS_FILES_DELETES_PER_PASS:
                beyond_cap += 1
                continue

            attempts += 1
            try:
                await message.delete()
            except discord.NotFound:
                deleted += 1
            except discord.HTTPException as error:
                failures[f"{type(error).__name__}: {error}"] += 1
            else:
                deleted += 1

        if with_backups and not beyond_cap and not failures:
            cleared_until = now - timedelta(days=max(kind.days for kind in kinds))
            await asyncio.to_thread(
                logs_data.update_retention, {"cleared_until": cleared_until}
            )
    except Exception as error:
        stopped = f"{type(error).__name__}: {error}"

    return Cleared(
        deleted=deleted,
        left=beyond_cap + attempts - deleted,
        failures=dict(failures),
        stopped=stopped,
    )

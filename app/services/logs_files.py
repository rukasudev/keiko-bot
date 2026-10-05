"""Keiko's own files on the logs files channel and how long each kind stays there: a
backup 30 days, a daily log 90, the monthly events archive and anything else for good."""
import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import discord

from app.constants import Commands
from app.data import logs_files as logs_files_data
from app.services.trace import as_utc


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


def expiring_kinds(with_backups: bool) -> List[Kind]:
    """What a pass deletes once old: the daily logs, and the backups on a day one went out."""
    kinds = [
        Kind(
            r"keiko_logs_\d{4}-\d{2}-\d{2}\.jsonl\.gz|keiko_log\.log",
            Commands.DAILY_LOGS_RETENTION_DAYS,
        )
    ]

    if with_backups:
        kinds.append(
            Kind(
                r"keiko_backup_\d{4}-\d{2}-\d{2}(?:_\d+-of-\d+)?\.zip\.age",
                Commands.BACKUP_RETENTION_DAYS,
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
        retention = await asyncio.to_thread(logs_files_data.find_retention)
        async for message in channel.history(
            limit=None,
            after=as_utc(retention["cleared_until"]) if retention else None,
            before=now - timedelta(days=min(kind.days for kind in kinds)),
            oldest_first=True,
        ):
            kind = kind_of(message, author_id, kinds)
            if kind is None or message.created_at > now - timedelta(days=kind.days):
                continue

            if attempts >= Commands.LOGS_FILES_DELETES_PER_PASS:
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
                logs_files_data.update_retention, {"cleared_until": cleared_until}
            )
    except Exception as error:
        stopped = f"{type(error).__name__}: {error}"

    return Cleared(
        deleted=deleted,
        left=beyond_cap + attempts - deleted,
        failures=dict(failures),
        stopped=stopped,
    )

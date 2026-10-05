"""The daily backup: Keiko's own collections, as Extended JSON Lines in zips sealed to
an age public key, so only the matching private key opens them."""
import io
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from bson import json_util
from pyrage import x25519

from app.constants import DBConfigs
from app.data import backup as backup_data
from app.data.indexes import INDEXES, located
from app.services import sealing
from app.services.logs_archive import ADMIN_LOCALE, part_names
from app.services.utils import ml


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
        present = backup_data.collection_names(database)
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
    budget = sealing.plaintext_limit(limit)
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
        encrypted = sealing.seal(payload, recipient)
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
            for document in backup_data.iter_documents(database, collection):
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

"""Reads the two shapes a Keiko log file can have.

The Discord logs channel holds years of daily attachments, and they are not all
the same: everything before the Mongo sink is the plain-text render of
`OptionalGuildIDFormatter`, and everything after it is the gzipped JSON Lines
export. One index has to hold both, so both land on the same record shape.

The text format loses what it never wrote — there is no session id in a 2023
file — and that is expected, not a parse failure.
"""
import gzip
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

# "[INFO] 2026-08-20 14:23:01 (guild_id: 123) - message"
TEXT_LINE = re.compile(
    r"^\[(?P<level>[A-Z]+)\] "
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"(?: \((?P<context_key>interaction_id|guild_id): (?P<context_value>\d+)\))?"
    r" - (?P<message>.*)$"
)

FIELDS = (
    "ts", "level", "message", "log_type", "guild_id", "user_id", "interaction_id",
    "channel_id", "feature", "source", "session_id", "trace_id", "module",
    "function", "line", "traceback", "env",
)


def empty_record() -> Dict[str, Any]:
    return {field: None for field in FIELDS}


def parse_jsonl_gz(payload: bytes) -> Iterator[Dict[str, Any]]:
    """The structured export: already the shape the store wants."""
    for line in gzip.decompress(payload).decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        record = empty_record()
        record.update({key: document.get(key) for key in FIELDS if key in document})
        yield record


def parse_text_log(payload: bytes) -> Iterator[Dict[str, Any]]:
    """The legacy render. Lines that match nothing belong to the entry above.

    That is how a traceback survives: `logging` writes it as continuation lines
    under the record it belongs to, so appending them to the open record is what
    reassembles the thing the Discord embed had to truncate.
    """
    current: Optional[Dict[str, Any]] = None
    overflow: List[str] = []

    for raw in payload.decode("utf-8", errors="replace").splitlines():
        match = TEXT_LINE.match(raw)

        if not match:
            if current is not None and raw.strip():
                overflow.append(raw)
            continue

        if current is not None:
            yield _close(current, overflow)
            overflow = []

        current = empty_record()
        current.update({
            "ts": _parse_timestamp(match.group("ts")),
            "level": match.group("level"),
            "message": match.group("message"),
        })
        if match.group("context_key"):
            current[match.group("context_key")] = match.group("context_value")

    if current is not None:
        yield _close(current, overflow)


def _close(record: Dict[str, Any], overflow: List[str]) -> Dict[str, Any]:
    if overflow:
        record["traceback"] = "\n".join(overflow)
    return record


def _parse_timestamp(value: str) -> str:
    return (
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc)
        .isoformat()
    )


def parse_attachment(filename: str, payload: bytes) -> Iterator[Dict[str, Any]]:
    if filename.endswith(".jsonl.gz") or filename.endswith(".jsonl.gzip"):
        return parse_jsonl_gz(payload)
    return parse_text_log(payload)

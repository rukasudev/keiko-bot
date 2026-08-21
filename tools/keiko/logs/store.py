"""The local index the agent actually queries.

SQLite with FTS5 over message and traceback. It holds the cold tail — anything
older than the 30 days Mongo keeps — rebuilt from the daily files on the
Discord logs channel. Losing this file costs nothing: re-run the backfill.

Idempotence keys on the Discord message id, never on the filename. Every nightly
attachment is literally `logs/keiko_log.log` — `doRollover` posts `baseFilename`
as-is — so 745 days of history arrive under one name. A filename key skips 744 of
them and reports success.
"""
import json
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_PATH = os.path.expanduser("~/.keiko/logs.db")

# Bumped when the shape changes. The index is rebuilt from Discord, never a
# source of truth, so an old one is discarded instead of migrated.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id            INTEGER PRIMARY KEY,
    ts            TEXT,
    level         TEXT,
    message       TEXT,
    log_type      TEXT,
    guild_id      TEXT,
    user_id       TEXT,
    interaction_id TEXT,
    channel_id    TEXT,
    feature       TEXT,
    source        TEXT,
    session_id    TEXT,
    trace_id      TEXT,
    module        TEXT,
    function      TEXT,
    line          INTEGER,
    traceback     TEXT,
    env           TEXT,
    origin        TEXT NOT NULL,
    origin_ref    TEXT NOT NULL,
    UNIQUE(origin, origin_ref)
);
CREATE INDEX IF NOT EXISTS entries_ts ON entries(ts);
CREATE INDEX IF NOT EXISTS entries_level_ts ON entries(level, ts);
CREATE INDEX IF NOT EXISTS entries_guild_ts ON entries(guild_id, ts);
CREATE INDEX IF NOT EXISTS entries_session ON entries(session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
USING fts5(message, traceback, content='entries', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, message, traceback)
    VALUES (new.id, new.message, new.traceback);
END;

CREATE TABLE IF NOT EXISTS synced_files (
    message_id  TEXT PRIMARY KEY,
    filename    TEXT,
    entries     INTEGER,
    synced_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

COLUMNS = (
    "ts", "level", "message", "log_type", "guild_id", "user_id", "interaction_id",
    "channel_id", "feature", "source", "session_id", "trace_id", "module",
    "function", "line", "traceback", "env",
)


def connect(path: str = DEFAULT_PATH) -> sqlite3.Connection:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    _reset_if_stale(connection)
    connection.executescript(SCHEMA)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return connection


def _reset_if_stale(connection: sqlite3.Connection) -> None:
    """An index built by an older shape is dropped, not migrated.

    Everything here is rebuilt from the Discord channel by re-running `sync`, so
    discarding is cheaper and safer than a migration path nobody will test.
    """
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return

    existing = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
        )
    ]
    if not existing:
        return

    connection.executescript(
        "DROP TRIGGER IF EXISTS entries_ai;"
        "DROP TABLE IF EXISTS entries_fts;"
        "DROP TABLE IF EXISTS entries;"
        "DROP TABLE IF EXISTS synced_files;"
    )
    connection.commit()


def already_synced(connection: sqlite3.Connection, message_id: str) -> bool:
    """Keyed on the message, because every attachment shares one filename."""
    row = connection.execute(
        "SELECT 1 FROM synced_files WHERE message_id = ?", (str(message_id),)
    ).fetchone()
    return row is not None


def insert_entries(
    connection: sqlite3.Connection,
    records: Iterable[Dict[str, Any]],
    origin: str,
) -> int:
    """Returns how many rows were new; re-running the same origin adds none.

    `origin` must be the Discord message id. Using the filename would make every
    day collide on `UNIQUE(origin, origin_ref)`, so two days holding the same
    line would keep only one of them.
    """
    inserted = 0
    statement = (
        f"INSERT OR IGNORE INTO entries ({', '.join(COLUMNS)}, origin, origin_ref) "
        f"VALUES ({', '.join('?' * len(COLUMNS))}, ?, ?)"
    )

    for index, record in enumerate(records):
        values = [record.get(column) for column in COLUMNS]
        cursor = connection.execute(statement, values + [origin, f"{origin}#{index}"])
        inserted += cursor.rowcount

    connection.commit()
    return inserted


def mark_synced(
    connection: sqlite3.Connection, message_id: str, filename: str, entries: int
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO synced_files (message_id, filename, entries) "
        "VALUES (?, ?, ?)",
        (str(message_id), filename, entries),
    )
    connection.commit()


def query(
    connection: sqlite3.Connection,
    text: Optional[str] = None,
    level: Optional[str] = None,
    guild_id: Optional[str] = None,
    session_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
) -> List[sqlite3.Row]:
    clauses, params = [], []

    if text:
        clauses.append(
            "id IN (SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?)"
        )
        params.append(text)
    if level:
        clauses.append("level = ?")
        params.append(level.upper())
    if guild_id:
        clauses.append("guild_id = ?")
        params.append(str(guild_id))
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    if until:
        clauses.append("ts <= ?")
        params.append(until)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return connection.execute(
        f"SELECT * FROM entries {where} ORDER BY ts DESC LIMIT ?", params + [limit]
    ).fetchall()


def signatures(
    connection: sqlite3.Connection,
    level: str = "ERROR",
    since: Optional[str] = None,
    limit: int = 20,
) -> List[sqlite3.Row]:
    """Group repeated failures instead of printing the same line 300 times.

    An agent reading this has a context budget; 300 copies of one stack trace
    spends it without adding information.
    """
    clauses, params = ["level = ?"], [level.upper()]
    if since:
        clauses.append("ts >= ?")
        params.append(since)

    return connection.execute(
        f"""
        SELECT
            COUNT(*) AS occurrences,
            MIN(ts) AS first_seen,
            MAX(ts) AS last_seen,
            COUNT(DISTINCT guild_id) AS guilds,
            substr(message, 1, 160) AS signature,
            MAX(module) AS module
        FROM entries
        WHERE {' AND '.join(clauses)}
        GROUP BY signature
        ORDER BY occurrences DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()


def summary(connection: sqlite3.Connection) -> Dict[str, Any]:
    row = connection.execute(
        "SELECT COUNT(*) AS total, MIN(ts) AS oldest, MAX(ts) AS newest FROM entries"
    ).fetchone()
    files = connection.execute("SELECT COUNT(*) AS files FROM synced_files").fetchone()
    levels = connection.execute(
        "SELECT level, COUNT(*) AS total FROM entries GROUP BY level ORDER BY total DESC"
    ).fetchall()

    return {
        "total": row["total"],
        "oldest": row["oldest"],
        "newest": row["newest"],
        "files": files["files"],
        "levels": {item["level"]: item["total"] for item in levels},
    }


def to_json(rows: Iterable[sqlite3.Row]) -> str:
    return json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2)

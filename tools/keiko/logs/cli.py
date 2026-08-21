"""`python -m tools.keiko logs <command>` — the cold-tail log reader.

Answers the question this whole thing exists for: what did Keiko do, and why
did it break, further back than the 30 days Mongo keeps. Recent logs are better
read straight from Mongo; this is for everything older, rebuilt from the daily
files on the Discord logs channel.

Output is written for an agent as much as for a person: `errors` groups repeats
into one line with a count rather than printing the same stack trace hundreds of
times, and `--json` exists so nothing has to be scraped out of a table.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from tools.keiko.logs import fetch, parse, store


def parse_since(value: Optional[str]) -> Optional[str]:
    """Accepts `24h`, `7d`, or an ISO date."""
    if not value:
        return None

    now = datetime.now(timezone.utc)
    units = {"h": "hours", "d": "days", "w": "weeks"}
    unit = units.get(value[-1])

    if unit and value[:-1].isdigit():
        return (now - timedelta(**{unit: int(value[:-1])})).isoformat()
    return value


def describe(attachment, records) -> str:
    """Every attachment is named `keiko_log.log`, so the date is what identifies it."""
    for record in records:
        if record.get("ts"):
            return str(record["ts"])[:10]
    return f"message {attachment['message_id']}"


def command_sync(args) -> int:
    connection = store.connect(args.db)
    seen = added = files = 0

    for attachment in fetch.iter_log_attachments():
        seen += 1
        message_id = attachment["message_id"]

        if not args.force and store.already_synced(connection, message_id):
            if args.incremental:
                print(f"Reached an already indexed message ({message_id}). Stopping.")
                break
            continue

        payload = fetch.download(attachment["url"])
        records = list(parse.parse_attachment(attachment["filename"], payload))
        inserted = store.insert_entries(connection, records, message_id)
        store.mark_synced(connection, message_id, attachment["filename"], len(records))

        files += 1
        added += inserted
        print(f"  {describe(attachment, records)}: {len(records)} parsed, {inserted} new")

        if args.limit and files >= args.limit:
            print(f"Stopped at --limit {args.limit}; more files remain unindexed.")
            break

    print(f"\n{files} file(s) indexed, {added} new entries, {seen} attachment(s) seen.")
    return 0


def command_query(args) -> int:
    connection = store.connect(args.db)
    rows = store.query(
        connection,
        text=args.text,
        level=args.level,
        guild_id=args.guild,
        session_id=args.session,
        since=parse_since(args.since),
        limit=args.limit,
    )

    if args.json:
        print(store.to_json(rows))
        return 0

    if not rows:
        print("No entries matched.")
        return 0

    for row in rows:
        print(f"{row['ts']} [{row['level']}] {row['message']}")
        detail = " ".join(
            f"{key}={row[key]}"
            for key in ("guild_id", "session_id", "module")
            if row[key]
        )
        if detail:
            print(f"    {detail}")
        if row["traceback"] and args.traceback:
            print("    " + row["traceback"].replace("\n", "\n    "))
    return 0


def command_errors(args) -> int:
    connection = store.connect(args.db)
    rows = store.signatures(
        connection, level=args.level, since=parse_since(args.since), limit=args.limit
    )

    if args.json:
        print(store.to_json(rows))
        return 0

    if not rows:
        print("No errors in that window.")
        return 0

    print(f"{'count':>6}  {'guilds':>6}  last seen             signature")
    for row in rows:
        print(
            f"{row['occurrences']:>6}  {row['guilds']:>6}  {row['last_seen'][:19]}  "
            f"{row['signature']}"
        )
    return 0


def command_status(args) -> int:
    data = store.summary(store.connect(args.db))
    print(f"database : {args.db}")
    print(f"entries  : {data['total']}")
    print(f"files    : {data['files']}")
    print(f"range    : {data['oldest']} .. {data['newest']}")
    for level, total in data["levels"].items():
        print(f"  {level:<9}{total}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.keiko logs")
    parser.add_argument("--db", default=store.DEFAULT_PATH)

    # `--db` also on every subcommand, because putting it after the verb is the
    # first thing anyone types. SUPPRESS keeps the subparser from overwriting a
    # value already given before the verb.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="index daily log files from Discord", parents=[common])
    sync.add_argument("--limit", type=int, default=0, help="stop after N files")
    sync.add_argument("--force", action="store_true", help="re-index indexed files")
    sync.add_argument(
        "--incremental",
        action="store_true",
        help="stop at the first already-indexed file (daily use)",
    )
    sync.set_defaults(handler=command_sync)

    query = subparsers.add_parser("query", help="search indexed logs", parents=[common])
    query.add_argument("text", nargs="?", help="full-text match on message/traceback")
    query.add_argument("--level")
    query.add_argument("--guild")
    query.add_argument("--session", help="every line of one interaction")
    query.add_argument("--since", help="24h, 7d, or an ISO date")
    query.add_argument("--limit", type=int, default=50)
    query.add_argument("--traceback", action="store_true")
    query.add_argument("--json", action="store_true")
    query.set_defaults(handler=command_query)

    errors = subparsers.add_parser("errors", help="repeated failures, grouped", parents=[common])
    errors.add_argument("--level", default="ERROR")
    errors.add_argument("--since", help="24h, 7d, or an ISO date")
    errors.add_argument("--limit", type=int, default=20)
    errors.add_argument("--json", action="store_true")
    errors.set_defaults(handler=command_errors)

    status = subparsers.add_parser("status", help="what the local index holds", parents=[common])
    status.set_defaults(handler=command_status)

    return parser


def main(argv: List[str]) -> int:
    if argv and argv[0] == "logs":
        argv = argv[1:]

    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except fetch.FetchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

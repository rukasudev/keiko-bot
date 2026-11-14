"""The CLI of the Mongo migrations and of the Redis cleanup and check."""

import argparse
import sys
from typing import Any, List, Optional, TextIO

from app import connect_redis
from app.config import is_production, mongo_url, redis_url
from app.migrations import redis_keys, runner


def parser() -> argparse.ArgumentParser:
    """The commands and their options."""
    commands = argparse.ArgumentParser(
        prog="python -m app.migrations",
        description="Apply and check the versioned MongoDB migrations.",
    )
    chosen = commands.add_subparsers(dest="command", required=True)
    chosen.add_parser(
        "status", help="each file of each folder: applied, pending or newer"
    )
    migrate = chosen.add_parser(
        "migrate", help="apply every pending schema file, then every pending data file"
    )
    migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="run the checks and each data file in an aborted transaction",
    )
    chosen.add_parser("verify", help="compare the database with what schema/ declares")
    snapshot = chosen.add_parser("snapshot", help="print the database's structure")
    snapshot.add_argument(
        "--declared",
        action="store_true",
        help="print what schema/ declares instead, without Mongo",
    )
    cleanup = chosen.add_parser(
        "redis-cleanup", help="unlink the Redis keys of every retired family"
    )
    cleanup.add_argument(
        "--dry-run", action="store_true", help="count the keys without unlinking them"
    )
    chosen.add_parser(
        "redis-check",
        help="fail on a key without a TTL, warn on one outside the four roles",
    )
    return commands


def main(
    argv: Optional[List[str]] = None,
    client: Any = None,
    out: TextIO = sys.stdout,
    redis_client: Any = None,
) -> int:
    """Run one command; 0 when every step passed, 1 otherwise."""
    args = parser().parse_args(argv)
    report = runner.Report(out)
    if args.command.startswith("redis-"):
        return _redis(args, redis_client, report)

    versions, problems = runner.read_versions()
    for problem in problems:
        report.line("fail", problem)
    if problems:
        return 1
    if args.command == "snapshot" and args.declared:
        out.write(runner.render(runner.expected_structure(versions)))
        return 0

    if client is None:
        url = mongo_url()
        if not url:
            report.line("fail", "no Mongo URL: set MONGO_URL (SSM in production)")
            return 1
        client = runner.connect(url, is_production())

    if args.command == "status":
        passed = runner.status(client, versions, report)
    elif args.command == "migrate":
        passed = runner.migrate(client, versions, report, dry_run=args.dry_run)
    elif args.command == "verify":
        expected = runner.expected_structure(versions)
        passed = runner.verify(client, expected, report)
    else:
        out.write(runner.render(runner.live_structure(client)))
        passed = True
    return 0 if passed and not report.failed else 1


def _redis(args: argparse.Namespace, client: Any, report: runner.Report) -> int:
    if client is None:
        url = redis_url()
        if not url:
            report.line("fail", "no Redis URL: set REDIS_URL (SSM in production)")
            return 1

        client = connect_redis(url)

    if args.command == "redis-cleanup":
        passed = redis_keys.cleanup(client, report, dry_run=args.dry_run)
    else:
        passed = redis_keys.check(client, report)
    return 0 if passed and not report.failed else 1


if __name__ == "__main__":
    sys.exit(main())

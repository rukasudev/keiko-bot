"""The Redis cleanup of retired key families and the check that every key has a role."""

from typing import Any, Iterator, List

from app.constants import Migrations as constants
from app.migrations.runner import Report


def cleanup(client: Any, report: Report, dry_run: bool = False) -> bool:
    """Unlink every key of a retired family, or only count them on a dry run."""
    verb = "would unlink" if dry_run else "unlinked"
    for pattern in constants.REDIS_RETIRED_PATTERNS:
        found = 0
        for batch in _batches(client, pattern):
            found += len(batch)
            if not dry_run:
                client.unlink(*batch)

        report.line("ok", f"{pattern}: {verb} {found} key(s)")
    return True


def check(client: Any, report: Report) -> bool:
    """Fail on a key that never expires; list, as a warning, a key outside the four
    roles that expires on its own."""
    outside: List[str] = []
    lasting: List[str] = []
    for batch in _batches(client, "*"):
        pipeline = client.pipeline(transaction=False)
        for key in batch:
            pipeline.ttl(key)

        for key, seconds in zip(batch, pipeline.execute()):
            if seconds == -1:
                lasting.append(key)
            elif not key.startswith(constants.REDIS_ROLE_PREFIXES):
                outside.append(key)

    roles = ", ".join(prefix.rstrip(":") for prefix in constants.REDIS_ROLE_PREFIXES)
    _tell(report, "warn", outside, f"with no role prefix ({roles}), left to expire")
    _tell(report, "fail", lasting, "without a TTL")
    return not lasting


def _tell(report: Report, status: str, keys: List[str], problem: str) -> None:
    if not keys:
        report.line("ok", f"no key {problem}")
        return

    shown = sorted(keys)[: constants.REDIS_CHECK_SAMPLE]
    report.line(status, f"{len(keys)} key(s) {problem}: {', '.join(shown)}")


def _batches(client: Any, pattern: str) -> Iterator[List[str]]:
    batch: List[str] = []
    for key in client.scan_iter(match=pattern, count=constants.REDIS_SCAN_BATCH):
        batch.append(key)
        if len(batch) == constants.REDIS_SCAN_BATCH:
            yield batch
            batch = []

    if batch:
        yield batch

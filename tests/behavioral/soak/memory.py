"""What a soak run retains: tracemalloc snapshots, each growth named by the line of
`app/` that allocated it, and the process RSS beside them."""

import gc
import os
import subprocess
import sys
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]
APP = str(ROOT / "app") + os.sep
TESTS = str(ROOT / "tests") + os.sep
DEPTH = 16
DISCORD = f"{os.sep}discord{os.sep}"
MOCK = f"{os.sep}unittest{os.sep}mock.py"
HARNESS_MOCK = "tests/(unittest.mock)"

IGNORED = (
    tracemalloc.Filter(False, tracemalloc.__file__),
    tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
    tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
    tracemalloc.Filter(False, "<unknown>"),
)


def rss_kib() -> Optional[int]:
    """The resident set of this process, in KiB, or None where it cannot be read."""
    statm = Path("/proc/self/statm")
    if statm.exists():
        pages = int(statm.read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") // 1024
    try:
        answer = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return int(answer.stdout.strip() or 0) or None


def site_of(frames: tracemalloc.Traceback) -> str:
    """The line an allocation is charged to: the newest frame of `app/`, unless the
    harness or a mock allocated it on the way; discord.py is charged to its caller."""
    library = None
    for frame in reversed(frames):
        if frame.filename.startswith(APP):
            return _short(frame)
        if frame.filename.startswith(TESTS) or frame.filename.endswith(MOCK):
            return _short(frame) if frame.filename.startswith(TESTS) else HARNESS_MOCK
        if library is None and DISCORD in frame.filename:
            library = frame
    return _short(library or frames[-1])


def is_harness(site: str) -> bool:
    """Growth the harness itself keeps (its mocks, its transcripts), not Keiko."""
    return site.startswith("tests/")


def _short(frame: tracemalloc.Frame) -> str:
    name = frame.filename
    if name.startswith(str(ROOT) + os.sep):
        name = name[len(str(ROOT)) + 1 :]
    elif "site-packages" + os.sep in name:
        name = name.split("site-packages" + os.sep, 1)[1]
    elif name.startswith(sys.prefix) or name.startswith(sys.base_prefix):
        name = "python/" + os.path.basename(name)
    return f"{name}:{frame.lineno}"


@dataclass
class Checkpoint:
    """The memory after `iteration` iterations."""

    iteration: int
    traced_kib: float
    rss_kib: Optional[int]
    snapshot: Optional[tracemalloc.Snapshot] = field(repr=False)


@dataclass
class Growth:
    """What one window of iterations kept, by allocating line."""

    iterations: int
    by_site: List[Tuple[str, int, int]]

    @property
    def keiko_bytes(self) -> int:
        return sum(size for site, size, _ in self.by_site if not is_harness(site))

    @property
    def harness_bytes(self) -> int:
        return sum(size for site, size, _ in self.by_site if is_harness(site))

    @property
    def keiko_bytes_per_iteration(self) -> float:
        return self.keiko_bytes / max(1, self.iterations)

    def top(self, count: int = 12, harness: bool = False) -> List[Tuple[str, int, int]]:
        return [row for row in self.by_site if is_harness(row[0]) == harness][:count]


class Recorder:
    """Starts tracemalloc and keeps one checkpoint per call, with the snapshot of
    the first and of the latest only, so the run itself does not grow."""

    def __init__(self) -> None:
        self.checkpoints: List[Checkpoint] = []
        self._started_here = False

    def __enter__(self) -> "Recorder":
        if not tracemalloc.is_tracing():
            tracemalloc.start(DEPTH)
            self._started_here = True
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._started_here:
            tracemalloc.stop()

    def checkpoint(self, iteration: int) -> Checkpoint:
        gc.collect()
        snapshot = tracemalloc.take_snapshot().filter_traces(IGNORED)
        traced = sum(stat.size for stat in snapshot.statistics("filename")) / 1024
        if len(self.checkpoints) > 1:
            self.checkpoints[-1].snapshot = None
        point = Checkpoint(iteration, traced, rss_kib(), snapshot)
        self.checkpoints.append(point)
        return point

    def window(self) -> "Growth":
        """What the run kept from its first checkpoint to its latest."""
        return growth(self.checkpoints[0], self.checkpoints[-1])


def growth(before: Checkpoint, after: Checkpoint) -> Growth:
    """What `after` holds that `before` did not, summed by allocating line."""
    assert before.snapshot is not None and after.snapshot is not None
    sites: Dict[str, List[int]] = {}
    for diff in after.snapshot.compare_to(before.snapshot, "traceback"):
        if not diff.size_diff:
            continue
        totals = sites.setdefault(site_of(diff.traceback), [0, 0])
        totals[0] += diff.size_diff
        totals[1] += diff.count_diff
    rows = sorted(
        ((site, size, count) for site, (size, count) in sites.items()),
        key=lambda row: row[1],
        reverse=True,
    )
    return Growth(after.iteration - before.iteration, rows)


def report(recorder: Recorder, window: Growth) -> str:
    """The curve and the lines that grew, as the run prints them."""
    lines = ["iteration  traced KiB  RSS KiB"]
    for point in recorder.checkpoints:
        rss = "-" if point.rss_kib is None else str(point.rss_kib)
        lines.append(f"{point.iteration:>9}  {point.traced_kib:>10.1f}  {rss:>7}")
    lines.append(
        f"window of {window.iterations} iterations: Keiko kept "
        f"{window.keiko_bytes} B ({window.keiko_bytes_per_iteration:.1f} B per "
        f"iteration), the harness {window.harness_bytes} B"
    )
    for site, size, count in window.top():
        lines.append(f"  {size:>+10} B {count:>+7} blocks  {site}")
    lines.append("the harness:")
    for site, size, count in window.top(5, harness=True):
        lines.append(f"  {size:>+10} B {count:>+7} blocks  {site}")
    return "\n".join(lines)

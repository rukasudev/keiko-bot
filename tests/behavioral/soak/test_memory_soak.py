"""The soak: Keiko's real code paths run again and again keep no more memory.

Each iteration is one golden path in turn plus a server's day (setups and panels
left open by a new member, messages through the listener, a member joining, a
slash command, log records, the flushes and the form sweep; see `traffic.py`).
After a warm-up that fills every bounded cache, tracemalloc checkpoints are taken
every `KEIKO_SOAK_CHECKPOINT` iterations; from the first to the last, the run
must keep at most `KEIKO_SOAK_MAX_BYTES` per iteration, charged to the line of
`app/` that allocated it.

Outside the default run (marker `soak`, deselected by `pytest.ini`). Run it with
`make soak` or `python -m scripts.keiko soak --minutes 600` for the rehearsal.
"""

import csv
import os
import time
from pathlib import Path

import pytest

from tests.behavioral.soak.memory import Recorder, report
from tests.behavioral.soak.traffic import Traffic

pytestmark = pytest.mark.soak

ITERATIONS = int(os.environ.get("KEIKO_SOAK_ITERATIONS", "600"))
MINUTES = float(os.environ.get("KEIKO_SOAK_MINUTES", "0"))
WARMUP = int(os.environ.get("KEIKO_SOAK_WARMUP", "150"))
CHECKPOINT = int(os.environ.get("KEIKO_SOAK_CHECKPOINT", "50"))
MAX_BYTES = float(os.environ.get("KEIKO_SOAK_MAX_BYTES", "256"))
REPORT = os.environ.get("KEIKO_SOAK_REPORT", "")
DRIVEN = {"blocked", "ping", "stream_elements", "hint", "welcome", "roles"}


def _keep_going(done: int, started: float) -> bool:
    if MINUTES:
        running = time.monotonic() - started < MINUTES * 60
        return running or done < WARMUP + 2 * CHECKPOINT
    return done < WARMUP + max(ITERATIONS, 2 * CHECKPOINT)


def _write_curve(recorder: Recorder) -> None:
    if not REPORT:
        return
    path = Path(REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["iteration", "traced_kib", "rss_kib"])
        for point in recorder.checkpoints:
            writer.writerow([point.iteration, f"{point.traced_kib:.1f}", point.rss_kib])


async def test_retained_memory_stays_flat(
    scenario_factory, deps, soak_logging, streamer_commands
):
    traffic = Traffic(scenario_factory, deps)
    started = time.monotonic()
    done = 0

    with Recorder() as recorder:
        while done < WARMUP:
            await traffic.iteration(done)
            done += 1
        recorder.checkpoint(done)
        while _keep_going(done, started):
            await traffic.iteration(done)
            done += 1
            if (done - WARMUP) % CHECKPOINT == 0:
                recorder.checkpoint(done)
                _write_curve(recorder)

    window = recorder.window()
    print("\n" + report(recorder, window))
    print(f"answered: {dict(traffic.answered)}")
    _write_curve(recorder)

    assert traffic.failures == [], (
        f"{len(traffic.failures)} steps failed, the soak did not drive them: "
        f"{traffic.failures[:5]}"
    )
    unanswered = DRIVEN - {kind for kind, count in traffic.answered.items() if count}
    assert unanswered == set(), f"the listeners never answered {unanswered}"
    assert window.keiko_bytes_per_iteration <= MAX_BYTES, report(recorder, window)

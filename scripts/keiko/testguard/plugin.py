"""The pytest plugin of a guard run: keep only the chosen tests, write each outcome."""

import json
import os
from typing import Dict, List

import pytest

SELECT = "TESTGUARD_SELECT"
REPORT = "TESTGUARD_REPORT"

_results: Dict[str, Dict[str, object]] = {}


def pytest_collection_modifyitems(
    config: pytest.Config, items: List[pytest.Item]
) -> None:
    """Keep the tests the select file lists, files in its order, tests in theirs."""
    path = os.environ.get(SELECT)
    if not path:
        return
    with open(path, encoding="utf-8") as handle:
        wanted = json.load(handle)
    order = {}
    for nodeid in wanted:
        order.setdefault(nodeid.split("::", 1)[0], len(order))
    chosen = set(wanted)
    kept = [item for item in items if item.nodeid in chosen]
    dropped = [item for item in items if item.nodeid not in chosen]
    kept.sort(key=lambda item: order.get(item.nodeid.split("::", 1)[0], len(order)))
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Add each phase's time to its test and keep the worst outcome."""
    entry = _results.setdefault(report.nodeid, {"outcome": "passed", "seconds": 0.0})
    entry["seconds"] = float(entry["seconds"]) + report.duration
    if report.failed:
        entry["outcome"] = "failed" if report.when == "call" else "error"
    elif report.skipped and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"


def pytest_sessionfinish() -> None:
    """Write every outcome to the report file, when the run asked for one."""
    path = os.environ.get(REPORT)
    if not path:
        return
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_results, handle)

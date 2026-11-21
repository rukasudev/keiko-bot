"""`python -m scripts.keiko testguard`: measure the suite, compare it to a baseline."""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from scripts.keiko.testguard import compare, ledger, measure, mutate

SECTIONS = ("coverage", "mutants", "changes")


def parse(argv: List[str]) -> argparse.Namespace:
    """The command line of the guard and of its ledger and mutant helpers."""
    parser = argparse.ArgumentParser(prog="python -m scripts.keiko testguard")
    parser.add_argument("--json", metavar="OUT", help="write the full measurement here")
    parser.add_argument(
        "--save-baseline", action="store_true", help="record this run as the baseline"
    )
    parser.add_argument("--work", help="folder for the copies (default: a temp folder)")
    parser.add_argument(
        "--keep", action="store_true", help="keep the copies afterwards"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, min(12, (os.cpu_count() or 4) - 2)),
        help="parallel mutant workers",
    )
    parser.add_argument(
        "--skip",
        default="",
        help=f"comma list of sections to skip: {', '.join(SECTIONS)}",
    )
    parser.add_argument(
        "command", nargs="*", help="`ledger [--sync]`, `show <mutant id>`, `mutants`"
    )
    parser.add_argument("--sync", action="store_true", help="ledger: add new bugs")
    return parser.parse_args(argv)


def ledger_command(sync: bool) -> int:
    """Check the ledger against the suite, or add the bugs it does not list yet."""
    if sync:
        added = ledger.sync(measure.ROOT)
        print(f"added {len(added)} entries to {ledger.LEDGER}")
        return 0
    found = ledger.check(measure.ROOT, ledger.load())
    print(f"{found.entries} entries, {len(found.missing)} without their test")
    for entry in found.missing:
        print(f"  {entry['id']}: {entry['test']} is gone ({entry['guarantee']})")
    for pin in found.unledgered:
        print(f"  not in the ledger: {pin.test} ({pin.guarantee})")
    for identifier in found.history_dropped:
        print(f"  {identifier}: its test no longer tells the bug")
    return 0 if found.ok else 1


def show_command(identifier: str) -> int:
    """Print one mutant: the original lines and the mutated ones."""
    mutant = mutate.find(measure.ROOT, identifier)
    if mutant is None:
        print(f"{identifier} does not apply to the current source")
        return 1
    source = (measure.ROOT / mutant.path).read_text(encoding="utf-8")
    mutated = mutate.apply(source, mutant)
    first, last = mutant.line - 1, mutant.end_line
    print(f"{mutant.path}:{mutant.line} ({mutant.operator})")
    for line in source.splitlines()[first:last]:
        print(f"- {line}")
    for line in mutated.splitlines()[first:last]:
        print(f"+ {line}")
    return 0


def mutants_command() -> int:
    """List the ids of the mutant set the guard runs."""
    baseline = compare.load_baseline()
    ids = list((baseline or {}).get("mutants", {}).get("status", {}))
    chosen, gone = measure.plan(measure.ROOT, ids or None)
    for mutant in chosen:
        print(mutant.id)
    for identifier in gone:
        print(f"gone: {identifier}")
    return 0


def _ledger_section(
    results: Optional[Dict[str, Dict[str, object]]],
) -> Dict[str, object]:
    entries = ledger.load()
    found = ledger.check(measure.ROOT, entries)
    section: Dict[str, object] = {
        "entries": found.entries,
        "missing": found.missing,
        "history_dropped": found.history_dropped,
        "unledgered": [pin.test for pin in found.unledgered],
    }
    if results is not None:
        outcomes: Dict[str, int] = {}
        failed = []
        for entry in entries:
            result = ledger.outcome(entry, results)
            outcomes[result] = outcomes.get(result, 0) + 1
            if result == "failed":
                failed.append(entry["id"])
        section["outcomes"] = dict(sorted(outcomes.items()))
        section["failed"] = failed
    return section


def _recheck(
    base: Path,
    work: Path,
    results: Dict[str, Dict[str, object]],
    mutants: List[mutate.Mutant],
    seconds: Dict[str, float],
    baseline: Optional[Dict],
    options,
) -> None:
    was = (baseline or {}).get("mutants", {}).get("status", {})
    doubtful = [
        mutant
        for mutant in mutants
        if was.get(mutant.id) in compare.KILLED
        and results[mutant.id]["status"] not in compare.KILLED
        and mutant.id not in options.accepted
    ]
    if not doubtful:
        return
    measure.log(f"mutants: {len(doubtful)} lost kills, run again on the whole suite")
    every = sorted(seconds)
    tests = {mutant.id: every for mutant in doubtful}
    again = measure.run_mutants(base, work, doubtful, tests, seconds, options.workers)
    for identifier, result in again.items():
        results[identifier] = {**result, "whole_suite": True}


def _time(base: Path, work: Path, current: Dict[str, object]) -> measure.Run:
    measure.log("time: the whole suite in one process, nothing else running")
    timed = measure.pytest(base, work, "suite", list(measure.SUITE))
    still, flaky = measure.confirmed(base, work, "suite", timed)
    current["suite"] = measure.suite_summary(timed, still, flaky)
    current["ledger"] = _ledger_section(timed.results)
    return timed


def _coverage_and_changes(
    base: Path, work: Path, options, current: Dict[str, object]
) -> Optional[Path]:
    measured = "coverage" not in options.skipped or "mutants" not in options.skipped
    jobs = [measure.coverage_job(base, work, "first")] if measured else []
    if "changes" not in options.skipped:
        jobs.extend(
            measure.change_job(base, work, change) for change in measure.CHANGES
        )
    measure.log(f"coverage and {len(jobs) - int(measured)} changes, at once")
    finished = measure.parallel(jobs, len(jobs))
    changes = finished[1:] if measured else finished
    if changes:
        current["changes"] = {
            change.key: result for change, result in zip(measure.CHANGES, changes)
        }
    if not measured:
        return None
    current["coverage_failures"] = len(finished[0].failing())
    data = work / "coverage-first.db"
    if "coverage" not in options.skipped:
        current["coverage"] = measure.coverage_summary(base, data, work)
    return data


def _settle_coverage(
    base: Path, work: Path, baseline: Optional[Dict], current: Dict[str, object]
) -> None:
    before = (baseline or {}).get("coverage")
    coverage = current.get("coverage")
    if not before or not coverage:
        return
    if not compare.lost_count(
        compare.lost_lines(coverage["modules"], before["modules"])
    ):
        return
    measure.log(
        "coverage: lines were lost, a second pass tells them from unstable ones"
    )
    measure.coverage_job(base, work, "again")()
    again = measure.coverage_summary(base, work / "coverage-again.db", work)
    current["coverage"] = measure.merged(coverage, again)


def _mutants(
    base: Path,
    work: Path,
    data: Path,
    timed: measure.Run,
    options,
    baseline: Optional[Dict],
    current: Dict[str, object],
) -> None:
    seconds = {
        nodeid: float(result["seconds"]) for nodeid, result in timed.results.items()
    }
    ids = list((baseline or {}).get("mutants", {}).get("status", {}))
    mutants, gone = measure.plan(base, ids or None)
    tests = measure.covering_tests(data, base, mutants, list(seconds))
    measure.log(f"mutants: {len(mutants)} on {options.workers} workers")
    results = measure.run_mutants(base, work, mutants, tests, seconds, options.workers)
    _recheck(base, work, results, mutants, seconds, baseline, options)
    current["mutants"] = measure.mutant_summary(results, gone)
    current["mutant_runs"] = results


def _report(current: Dict[str, object], baseline: Optional[Dict], options) -> int:
    comparison = compare.compare(current, baseline, options.accepted)
    print(compare.render(current, baseline, comparison))
    if options.json:
        output = json.dumps({"current": current, "comparison": comparison}, indent=2)
        if options.json == "-":
            print(output)
        else:
            Path(options.json).write_text(output, encoding="utf-8")
    if options.save_baseline:
        _save(current, baseline)
    return 0 if comparison["safe"] else 1


def run(options: argparse.Namespace) -> int:
    """Measure the current tree, compare it to the baseline and print the verdict."""
    started = time.monotonic()
    options.skipped = {name.strip() for name in options.skip.split(",") if name.strip()}
    options.accepted = compare.load_accepted()
    baseline = compare.load_baseline()
    work = measure.work_dir(options.work)
    measure.log(f"work folder {work}")
    current: Dict[str, object] = {
        "tool": "testguard 1",
        **measure.commit(measure.ROOT),
        "recorded": date.today().isoformat(),
    }
    try:
        base = measure.copy_tree(measure.ROOT, work / "base")
        timed = _time(base, work, current)
        data = _coverage_and_changes(base, work, options, current)
        _settle_coverage(base, work, baseline, current)
        if data is not None and "mutants" not in options.skipped:
            _mutants(base, work, data, timed, options, baseline, current)
        current["minutes"] = round((time.monotonic() - started) / 60, 1)
        return _report(current, baseline, options)
    finally:
        if not options.keep and not options.work:
            shutil.rmtree(work, ignore_errors=True)


def _save(current: Dict[str, object], previous: Optional[Dict]) -> None:
    kept = {key: value for key, value in current.items() if key != "mutant_runs"}
    compare.carry_unstable(kept, previous)
    compare.BASELINE.write_text(json.dumps(kept, indent=1) + "\n", encoding="utf-8")
    measure.log(f"baseline written to {compare.BASELINE}")


def main(argv: Optional[List[str]] = None) -> int:
    """Dispatch to the guard run or to one of its helpers."""
    options = parse(argv or [])
    if options.command[:1] == ["ledger"]:
        return ledger_command(options.sync)
    if options.command[:1] == ["show"] and len(options.command) == 2:
        return show_command(options.command[1])
    if options.command[:1] == ["mutants"]:
        return mutants_command()
    if options.command:
        print(f"unknown command: {' '.join(options.command)}", file=sys.stderr)
        return 2
    return run(options)

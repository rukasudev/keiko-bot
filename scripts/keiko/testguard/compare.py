"""Compare a guard measurement to the baseline, decide the verdict and print it."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from scripts.keiko.testguard.measure import lines_of, ranges

BASELINE = Path(__file__).with_name("baseline.json")

ACCEPTED = Path(__file__).with_name("accepted.yml")

ACCEPTED_FIELDS = ("id", "reason", "evidence")

KILLED = ("killed", "timeout")

Section = Dict[str, object]


@dataclass
class Verdict:
    """What made the tree unsafe, what only deserves a look, and the details."""

    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    found: Dict[str, object] = field(default_factory=dict)

    def result(self) -> Dict[str, object]:
        """The verdict as the JSON report writes it."""
        return {
            "safe": not self.reasons,
            "reasons": self.reasons,
            "warnings": self.warnings,
            **self.found,
        }


def load_baseline(path: Path = BASELINE) -> Optional[Section]:
    """The saved baseline, or None before the first one is recorded."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_accepted(path: Path = ACCEPTED) -> Dict[str, Dict[str, str]]:
    """The mutants whose baseline kill was shown false, by id, each with its reason."""
    if not path.exists():
        return {}
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    accepted: Dict[str, Dict[str, str]] = {}
    for entry in document.get("mutants") or []:
        lacking = [
            name for name in ACCEPTED_FIELDS if not str(entry.get(name, "")).strip()
        ]
        if lacking:
            named = entry.get("id", "an entry")
            raise ValueError(f"{path.name}: {named} has no {', '.join(lacking)}")
        accepted[entry["id"]] = entry
    return accepted


def _measured(module: Optional[Dict]) -> bool:
    return bool(module) and "statements" in module


def lost_lines(now: Dict[str, Dict], before: Dict[str, Dict]) -> Section:
    """Lines the baseline covered for sure that are not covered now, per module."""
    lost: Dict[str, List[int]] = {}
    changed: List[str] = []
    unmeasured: Dict[str, int] = {}
    for module, was in before.items():
        current = now.get(module)
        if not _measured(was):
            continue
        if not _measured(current):
            unmeasured[module] = int(was["covered"])
            continue
        if current["statements"] != was["statements"]:
            changed.append(module)
            continue
        gone = set(lines_of(current["missing"])) - set(lines_of(was["missing"]))
        gone -= set(lines_of(str(was.get("unstable", ""))))
        if gone:
            lost[module] = sorted(gone)
    return {"lines": lost, "changed_modules": changed, "unmeasured": unmeasured}


def lost_count(lost: Section) -> int:
    """How many lines a `lost_lines` result names, unmeasured modules included."""
    lines = sum(len(numbers) for numbers in lost.get("lines", {}).values())
    return lines + sum(lost.get("unmeasured", {}).values())


def carry_unstable(current: Section, previous: Optional[Section]) -> None:
    """Keep the unstable lines an earlier baseline of the same app/ already saw."""
    if not previous or previous.get("app_tree") != current.get("app_tree"):
        return
    before = (previous.get("coverage") or {}).get("modules", {})
    for name, module in (current.get("coverage") or {}).get("modules", {}).items():
        was = before.get(name) or {}
        if not was.get("unstable") or was.get("statements") != module.get("statements"):
            continue
        seen = set(lines_of(str(module.get("unstable", ""))))
        seen |= set(lines_of(was["unstable"]))
        module["unstable"] = ranges(seen)


def _gained_lines(now: Dict[str, Dict], before: Dict[str, Dict]) -> int:
    gained = 0
    for module, current in now.items():
        was = before.get(module)
        if _measured(was) and _measured(current):
            if current["statements"] == was["statements"]:
                missing = set(lines_of(was["missing"]))
                gained += len(missing - set(lines_of(current["missing"])))
    return gained


def _branch_drops(now: Dict[str, Dict], before: Dict[str, Dict]) -> Dict[str, List]:
    drops = {}
    for module, was in before.items():
        current = now.get(module) or {}
        if _measured(was) and _measured(current):
            if current["covered_branches"] < was["covered_branches"]:
                drops[module] = [was["covered_branches"], current["covered_branches"]]
    return drops


def _judge_suite(verdict: Verdict, current: Section) -> None:
    suite = current.get("suite")
    if not suite:
        return
    if suite.get("exit") not in (0, 1) or not suite["tests"]:
        verdict.reasons.append(
            f"the suite did not run whole (exit {suite.get('exit')})"
        )
    if suite["failing"]:
        verdict.reasons.append(
            f"{len(suite['failing'])} tests fail on the current tree"
        )
    if suite["flaky"]:
        verdict.warnings.append(f"{len(suite['flaky'])} tests failed once, then passed")


def _judge_ledger(verdict: Verdict, current: Section) -> None:
    book = current["ledger"]
    if book["missing"]:
        verdict.reasons.append(
            f"{len(book['missing'])} ledger entries name a test that is gone"
        )
    if book.get("failed"):
        verdict.reasons.append(f"{len(book['failed'])} ledger tests fail")
    if book["unledgered"]:
        verdict.warnings.append(
            f'{len(book["unledgered"])} "Broke as" docstrings are not in the ledger '
            "(python -m scripts.keiko testguard ledger --sync)"
        )
    if book["history_dropped"]:
        verdict.warnings.append(
            f"{len(book['history_dropped'])} pinned tests no longer tell their bug"
        )


def _judge_changes(verdict: Verdict, current: Section) -> None:
    changes = current.get("changes") or {}
    caught = changes.get("validator", {})
    if caught.get("applies") and caught.get("failing_tests", 0) == 0:
        verdict.reasons.append("no test catches the link validator accepting anything")
    for key, change in changes.items():
        if not change.get("applies"):
            verdict.warnings.append(f"the standard change {key} no longer applies")
        elif change.get("exit") not in (0, 1):
            verdict.warnings.append(
                f"the run of {key} stopped (exit {change.get('exit')})"
            )
    if current.get("coverage_failures"):
        failures = current["coverage_failures"]
        verdict.warnings.append(f"{failures} tests failed under coverage")


def _judge_coverage(verdict: Verdict, current: Section, before: Section) -> None:
    coverage = current.get("coverage")
    if not coverage or not before.get("coverage"):
        return
    now, was = coverage["modules"], before["coverage"]["modules"]
    lost = lost_lines(now, was)
    verdict.found["coverage_lost"] = lost
    verdict.found["coverage_gained_lines"] = _gained_lines(now, was)
    verdict.found["branch_drops"] = _branch_drops(now, was)

    total = lost_count(lost)
    if total:
        verdict.reasons.append(f"{total} lines covered in the baseline are not now")
    if verdict.found["branch_drops"]:
        drops = len(verdict.found["branch_drops"])
        verdict.warnings.append(f"{drops} modules cover fewer branches")
    if lost["changed_modules"]:
        changed = len(lost["changed_modules"])
        verdict.warnings.append(f"{changed} app modules changed since the baseline")


def _judge_mutants(
    verdict: Verdict, current: Section, before: Section, accepted: Dict[str, Dict]
) -> None:
    mutants = current.get("mutants")
    if not mutants or not before.get("mutants"):
        return
    was = before["mutants"]["status"]
    now = mutants["status"]
    lost = sorted(
        identifier
        for identifier, status in now.items()
        if was.get(identifier) in KILLED and status not in KILLED
    )
    verdict.found["accepted"] = {
        identifier: now[identifier]
        for identifier in sorted(accepted)
        if identifier in now
    }
    lost = [identifier for identifier in lost if identifier not in accepted]
    verdict.found["lost_kills"] = lost
    verdict.found["new_kills"] = sorted(
        identifier
        for identifier, status in now.items()
        if status in KILLED and identifier in was and was[identifier] not in KILLED
    )
    if lost:
        verdict.reasons.append(f"{len(lost)} mutants the baseline killed now survive")
    if mutants["gone"]:
        verdict.warnings.append(f"{len(mutants['gone'])} baseline mutants are gone")
    killed_again = [
        identifier
        for identifier, status in verdict.found["accepted"].items()
        if status in KILLED
    ]
    if killed_again:
        verdict.warnings.append(
            f"{len(killed_again)} accepted mutants are killed again: "
            "their entries in accepted.yml can go"
        )


def _judge_tree(verdict: Verdict, current: Section, before: Section) -> None:
    changed = before.get("app_tree") not in (None, current.get("app_tree"))
    if changed or current.get("app_dirty"):
        verdict.warnings.append(
            "app/ differs from the baseline's: the mutants and lines may have moved"
        )


def compare(
    current: Section,
    baseline: Optional[Section],
    accepted: Optional[Dict[str, Dict]] = None,
) -> Section:
    """What got worse since the baseline, and whether the tree is still safe."""
    verdict = Verdict()
    before = baseline or {}
    _judge_tree(verdict, current, before)
    _judge_suite(verdict, current)
    _judge_ledger(verdict, current)
    _judge_changes(verdict, current)
    _judge_coverage(verdict, current, before)
    _judge_mutants(verdict, current, before, accepted or {})
    return verdict.result()


def _was(baseline: Optional[Section], *keys: str) -> str:
    value: object = baseline
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return "-"
        value = value[key]
    return str(value)


def _coverage_lines(current: Section, baseline: Optional[Section], found: Section):
    coverage = current.get("coverage")
    yield "1. Coverage of app/ (line and branch; every module in --json)"
    if not coverage:
        yield "   not measured"
        return
    for kind in ("lines", "branches"):
        now = coverage[kind]
        was = _was(baseline, "coverage", kind, "percent")
        yield (
            f"   {kind:<9} {now['percent']:6.2f}%  {now['covered']}/{now['total']}"
            f"   baseline {was}%"
        )
    if not baseline:
        return
    lost = found.get("coverage_lost", {})
    lines, unmeasured = lost.get("lines", {}), lost.get("unmeasured", {})
    yield "   covered before and not now:" + ("" if lines or unmeasured else " none")
    for module, numbers in sorted(lines.items()):
        more = f" (+{len(numbers) - 20})" if len(numbers) > 20 else ""
        yield f"     {module}: {', '.join(str(n) for n in numbers[:20])}{more}"
    for module, count in sorted(unmeasured.items()):
        yield f"     {module}: no longer measured ({count} lines were covered)"
    yield f"   newly covered lines: {found.get('coverage_gained_lines', 0)}"


def _mutant_lines(current: Section, baseline: Optional[Section], found: Section):
    mutants = current.get("mutants")
    yield "2. Mutants (each against the tests that reach its lines)"
    if not mutants:
        yield "   not measured"
        return
    yield "   area        killed timeout survived uncovered error  baseline caught"
    earlier = (baseline or {}).get("mutants", {}).get("areas", {})
    for area, counts in mutants["areas"].items():
        was = earlier.get(area)
        before = f"{was['killed'] + was['timeout']}/{sum(was.values())}" if was else "-"
        yield (
            f"   {area:<11} {counts['killed']:6d} {counts['timeout']:7d} "
            f"{counts['survived']:8d} {counts['uncovered']:9d} {counts['error']:5d}"
            f"  {before}"
        )
    if baseline:
        lost = found.get("lost_kills", [])
        yield f"   killed before, surviving now: {len(lost) or 'none'}"
        yield from (f"     {identifier}" for identifier in lost)
        accepted = found.get("accepted", {})
        if accepted:
            yield (
                f"   accepted (their baseline kill was shown false, accepted.yml):"
                f" {len(accepted)}"
            )
            yield from (
                f"     {identifier} ({status})"
                for identifier, status in accepted.items()
            )
        yield f"   newly killed: {len(found.get('new_kills', []))}"


def _ledger_lines(current: Section, baseline: Optional[Section]):
    book = current["ledger"]
    yield "3. Past bugs pinned (scripts/keiko/testguard/ledger.yml)"
    outcomes = ", ".join(f"{n} {name}" for name, n in book.get("outcomes", {}).items())
    yield (
        f"   {book['entries']} entries, {len(book['missing'])} without their test"
        f"{'; their tests: ' + outcomes if outcomes else ''}"
        f"   baseline {_was(baseline, 'ledger', 'entries')} entries"
    )
    for entry in book["missing"]:
        yield f"     {entry['id']}: {entry['test']} is gone ({entry['guarantee']})"
    yield from (
        f"     {identifier}: its test fails" for identifier in book.get("failed", [])
    )


def _change_lines(current: Section, baseline: Optional[Section]):
    changes = current.get("changes")
    yield "4. Change cost (each change alone, on a copy, whole suite)"
    if not changes:
        yield "   not measured"
        return
    yield "   change           failing tests  files goldens  other  baseline"
    earlier = (baseline or {}).get("changes", {})
    for key, change in changes.items():
        if not change.get("applies"):
            yield f"   {key:<16} does not apply"
            continue
        was = earlier.get(key)
        before = f"{was['failing_tests']}/{was['goldens']}" if was else "-"
        yield (
            f"   {key:<16} {change['failing_tests']:13d} {change['failing_files']:6d}"
            f" {change['goldens']:7d} {change['other_tests']:6d}  {before}"
        )
    caught = changes.get("validator", {})
    if caught.get("applies"):
        yield f"   the validator change is caught by {caught['failing_tests']} tests:"
        yield from (f"     {nodeid}" for nodeid in caught["caught_by"])


def _time_lines(current: Section, baseline: Optional[Section]):
    suite = current.get("suite")
    yield "5. Time (the whole suite, one process, nothing else running)"
    if not suite:
        yield "   not measured"
        return
    outcomes = ", ".join(f"{n} {name}" for name, n in suite["outcomes"].items())
    yield (
        f"   {suite['tests']} tests ({outcomes}) in {suite['wall_seconds']} s"
        f"   baseline {_was(baseline, 'suite', 'wall_seconds')} s,"
        f" {_was(baseline, 'suite', 'tests')} tests"
    )
    yield "   slowest files:"
    for item in suite["slowest_files"]:
        yield f"     {item['seconds']:6.1f} s {item['tests']:4d} tests  {item['file']}"


def _verdict_lines(found: Section):
    if found["safe"]:
        yield "verdict: SAFE, nothing the baseline guarded was lost"
    else:
        yield "verdict: NOT SAFE"
        yield from (f"   - {reason}" for reason in found["reasons"])
    yield from (f"   warning: {warning}" for warning in found["warnings"])


def render(current: Section, baseline: Optional[Section], found: Section) -> str:
    """The plain-text report: five sections, then the verdict."""
    against = baseline["commit"] if baseline else "none recorded"
    dirty = " with uncommitted changes" if current["dirty"] else ""
    head = (
        f"testguard on {current['commit']} (app/ {current.get('app_tree')}){dirty}, "
        f"baseline {against}, {current['minutes']} min"
    )
    blocks = [
        [head],
        list(_coverage_lines(current, baseline, found)),
        list(_mutant_lines(current, baseline, found)),
        list(_ledger_lines(current, baseline)),
        list(_change_lines(current, baseline)),
        list(_time_lines(current, baseline)),
        list(_verdict_lines(found)),
    ]
    return "\n\n".join("\n".join(block) for block in blocks)

"""Run the suite on throwaway copies of the tree: time, coverage, mutants, changes."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from scripts.keiko.testguard import mutate
from scripts.keiko.testguard.plugin import REPORT, SELECT

ROOT = Path(__file__).resolve().parents[3]

SUITE = ("tests/",)

GOLDENS = "tests/behavioral/golden/"

SEED = 67

QUOTAS = {"forms": 84, "core": 66, "commands": 66, "migrations": 36}

SKIPPED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".wt"}


@dataclass(frozen=True)
class Change:
    """One standard change: a text edit of one app file, applied to a copy."""

    key: str
    title: str
    path: str
    old: str
    new: str


CHANGES = (
    Change(
        "edit_label",
        'The shared "Editar" button reads "Alterar"',
        "app/languages/buttons.yml",
        "    pt-br: Editar\n",
        "    pt-br: Alterar\n",
    ),
    Change(
        "activation_word",
        'One word of the activation message: "Ativei" reads "Liguei"',
        "app/languages/commands.yml",
        'pt-br: "Ativei o comando',
        'pt-br: "Liguei o comando',
    ),
    Change(
        "title_case",
        'The block links title reads "Bloquear links"',
        "app/commands/block_links/form.yml",
        'pt-br: ":no_entry_sign: Bloquear Links"',
        'pt-br: ":no_entry_sign: Bloquear links"',
    ),
    Change(
        "red_to_grey",
        "Every red button of the forms is drawn grey",
        "app/forms/discord/views.py",
        '"danger": discord.ButtonStyle.danger,',
        '"danger": discord.ButtonStyle.secondary,',
    ),
    Change(
        "validator",
        "The link validator accepts anything",
        "app/forms/form/responses/validations.py",
        "def _link_or_domain(value: Any, _context: ValidationContext) -> str | None:\n",
        "def _link_or_domain(value: Any, _context: ValidationContext) -> str | None:\n"
        "    return None\n",
    ),
)

CAUGHT = "validator"


@dataclass
class Run:
    """One pytest process: its exit code, its wall time and each test's outcome."""

    code: int
    seconds: float
    results: Dict[str, Dict[str, object]] = field(default_factory=dict)
    timed_out: bool = False

    def failing(self) -> List[str]:
        """The tests that failed or errored, in id order."""
        return sorted(
            nodeid
            for nodeid, result in self.results.items()
            if result["outcome"] in ("failed", "error")
        )


def log(message: str) -> None:
    """One progress line on stderr, so `--json -` stays clean."""
    print(
        f"[testguard {time.strftime('%H:%M:%S')}] {message}",
        file=sys.stderr,
        flush=True,
    )


def _tracked(source: Path) -> List[str]:
    try:
        listed = subprocess.run(
            ["git", "-C", str(source), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        return [name for name in listed.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        found = []
        for folder, subfolders, filenames in os.walk(source):
            subfolders[:] = [name for name in subfolders if name not in SKIPPED_DIRS]
            for filename in filenames:
                found.append(os.path.relpath(os.path.join(folder, filename), source))
        return found


def copy_tree(source: Path, target: Path) -> Path:
    """A copy of the working tree, tracked and untracked files, without git or venv."""
    target.mkdir(parents=True, exist_ok=True)
    for name in _tracked(source):
        origin = source / name
        if not origin.is_file() or origin.is_symlink():
            continue
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
    return target


def _git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *args], capture_output=True, check=True, text=True
    ).stdout.strip()


def commit(source: Path) -> Dict[str, object]:
    """HEAD, the tree hash of its app/, and whether the working tree differs from it."""
    try:
        return {
            "commit": _git(source, "rev-parse", "--short=8", "HEAD"),
            "app_tree": _git(source, "rev-parse", "--short=12", "HEAD:app"),
            "dirty": bool(
                _git(source, "status", "--porcelain", "--untracked-files=no")
            ),
            "app_dirty": bool(_git(source, "status", "--porcelain", "--", "app")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {
            "commit": "unknown",
            "app_tree": "unknown",
            "dirty": True,
            "app_dirty": True,
        }


def pytest(
    tree: Path,
    work: Path,
    name: str,
    args: Sequence[str],
    select: Optional[Sequence[str]] = None,
    timeout: Optional[float] = None,
    env: Optional[Dict[str, str]] = None,
) -> Run:
    """Run pytest in `tree` with the guard plugin; outcomes go to `work/<name>.json`."""
    report = work / f"{name}.json"
    report.unlink(missing_ok=True)
    variables = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        REPORT: str(report),
        **(env or {}),
    }
    if select is not None:
        chosen = work / f"{name}.select.json"
        chosen.write_text(json.dumps(list(select)), encoding="utf-8")
        variables[SELECT] = str(chosen)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *args,
        "-q",
        "--tb=no",
        "-p",
        "no:cacheprovider",
        "-p",
        "scripts.keiko.testguard.plugin",
        f"--basetemp={work / ('tmp-' + name)}",
    ]
    started = time.monotonic()
    with open(work / f"{name}.log", "w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=tree,
            env=variables,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            code = process.wait()
            timed_out = True
    seconds = time.monotonic() - started
    shutil.rmtree(work / f"tmp-{name}", ignore_errors=True)
    results = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    return Run(code=code, seconds=seconds, results=results, timed_out=timed_out)


def parallel(jobs: Iterable[Callable[[], object]], workers: int) -> List[object]:
    """Run the jobs on a pool of threads, each job one subprocess, in submit order."""
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(job) for job in jobs]
        return [future.result() for future in futures]


def confirmed(
    tree: Path, work: Path, name: str, run: Run
) -> Tuple[List[str], List[str]]:
    """The run's failures split into those that fail again alone and those that pass."""
    failing = run.failing()
    if not failing:
        return [], []
    again = pytest(tree, work, f"{name}-again", _files(failing, tree), select=failing)
    still = [nodeid for nodeid in failing if nodeid in again.failing()]
    return still, [nodeid for nodeid in failing if nodeid not in still]


def _files(nodeids: Iterable[str], tree: Path) -> List[str]:
    files = sorted({nodeid.split("::", 1)[0] for nodeid in nodeids})
    if not any(path.startswith("tests/") for path in files):
        anchor = min(tree.glob("tests/test_*.py"), default=None)
        if anchor is not None:
            files.append(str(anchor.relative_to(tree)))
    return sorted(files, key=lambda path: not path.startswith("tests/"))


def by_file(results: Dict[str, Dict[str, object]]) -> Dict[str, Tuple[float, int]]:
    """Seconds and test count of every test file in a run."""
    totals: Dict[str, Tuple[float, int]] = {}
    for nodeid, result in results.items():
        path = nodeid.split("::", 1)[0]
        seconds, count = totals.get(path, (0.0, 0))
        totals[path] = (seconds + float(result["seconds"]), count + 1)
    return totals


def suite_summary(run: Run, still: List[str], flaky: List[str]) -> Dict[str, object]:
    """The time section: counts, wall time and the twenty slowest files."""
    outcomes: Dict[str, int] = {}
    for result in run.results.values():
        outcomes[str(result["outcome"])] = outcomes.get(str(result["outcome"]), 0) + 1
    files = by_file(run.results)
    slowest = sorted(files.items(), key=lambda item: -item[1][0])[:20]
    return {
        "exit": run.code,
        "tests": len(run.results),
        "outcomes": dict(sorted(outcomes.items())),
        "failing": still,
        "flaky": flaky,
        "wall_seconds": round(run.seconds, 1),
        "test_seconds": round(sum(seconds for seconds, _ in files.values()), 1),
        "slowest_files": [
            {"file": path, "seconds": round(seconds, 1), "tests": count}
            for path, (seconds, count) in slowest
        ],
    }


def coverage_job(tree: Path, work: Path, tag: str) -> Callable[[], Run]:
    """The whole suite in one process, in its own order, under coverage by test."""

    def job() -> Run:
        return pytest(
            tree,
            work,
            f"coverage-{tag}",
            [
                *SUITE,
                "--cov=app",
                "--cov-branch",
                "--cov-context=test",
                "--cov-report=",
            ],
            env={"COVERAGE_FILE": str(work / f"coverage-{tag}.db")},
        )

    return job


def ranges(lines: Iterable[int]) -> str:
    """The ranges string, such as `3-5,9`, of a set of line numbers."""
    ordered = sorted(set(lines))
    parts = []
    start = previous = None
    for line in ordered:
        if previous is not None and line == previous + 1:
            previous = line
            continue
        if start is not None:
            parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = line
    if start is not None:
        parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(parts)


def lines_of(ranges: str) -> List[int]:
    """The line numbers a ranges string such as `3-5,9` names."""
    found: List[int] = []
    for part in filter(None, ranges.split(",")):
        first, _, last = part.partition("-")
        found.extend(range(int(first), int(last or first) + 1))
    return found


def is_app_module(path: str) -> bool:
    """Whether a measured file is app code, not one of the tests living beside it."""
    return path.startswith("app/") and not path.endswith("_test.py")


def coverage_summary(tree: Path, data: Path, work: Path) -> Dict[str, object]:
    """Line and branch coverage of every app module, with its missing lines."""
    import coverage

    measured = coverage.Coverage(data_file=str(data))
    measured.load()
    report = work / f"{data.name}.json"
    measured.json_report(outfile=str(report), ignore_errors=True)
    files = json.loads(report.read_text(encoding="utf-8"))["files"]

    modules: Dict[str, Dict[str, object]] = {}
    for name, detail in files.items():
        relative = os.path.relpath(name, tree) if os.path.isabs(name) else name
        if not is_app_module(relative):
            continue
        summary = detail["summary"]
        modules[relative] = {
            "statements": summary["num_statements"],
            "covered": summary["covered_lines"],
            "branches": summary.get("num_branches", 0),
            "covered_branches": summary.get("covered_branches", 0),
            "missing": ranges(detail["missing_lines"]),
        }
    for path in sorted(tree.glob("app/**/*.py")):
        relative = str(path.relative_to(tree))
        if is_app_module(relative) and relative not in modules:
            modules[relative] = {"unmeasured": True}
    return {"modules": dict(sorted(modules.items())), **totals(modules)}


def merged(first: Dict[str, object], second: Dict[str, object]) -> Dict[str, object]:
    """Two coverage passes as one: a line either pass ran is covered, a flip noted."""
    modules: Dict[str, Dict[str, object]] = {}
    for name, one in first["modules"].items():
        two = second["modules"].get(name, one)
        if "statements" not in one or "statements" not in two:
            modules[name] = one
            continue
        missing_one = set(lines_of(one["missing"]))
        missing_two = set(lines_of(two["missing"]))
        missing = missing_one & missing_two
        entry = {
            **one,
            "covered": int(one["statements"]) - len(missing),
            "covered_branches": max(one["covered_branches"], two["covered_branches"]),
            "missing": ranges(missing),
        }
        flipped = missing_one ^ missing_two
        if flipped:
            entry["unstable"] = ranges(flipped)
        modules[name] = entry
    return {"modules": modules, **totals(modules)}


def totals(modules: Dict[str, Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    """The line and branch totals of a coverage section's modules."""
    measured = [module for module in modules.values() if "statements" in module]
    lines = sum(int(module["statements"]) for module in measured)
    covered = sum(int(module["covered"]) for module in measured)
    branches = sum(int(module["branches"]) for module in measured)
    taken = sum(int(module["covered_branches"]) for module in measured)
    return {
        "lines": {
            "covered": covered,
            "total": lines,
            "percent": _percent(covered, lines),
        },
        "branches": {
            "covered": taken,
            "total": branches,
            "percent": _percent(taken, branches),
        },
    }


def _percent(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def area_files(tree: Path) -> Dict[str, List[str]]:
    """The modules each mutant area draws from: the code that matters most."""

    def listed(pattern: str, keep: Callable[[Path], bool]) -> List[str]:
        return sorted(
            str(path.relative_to(tree)) for path in tree.glob(pattern) if keep(path)
        )

    return {
        "forms": listed("app/forms/**/*.py", lambda _path: True),
        "core": listed(
            "app/core/**/*.py", lambda path: not path.name.endswith("_test.py")
        ),
        "commands": listed(
            "app/commands/**/*.py",
            lambda path: path.name not in ("cog.py", "__init__.py"),
        ),
        "migrations": listed("app/migrations/data/*.py", lambda _path: True),
    }


def area_of(identifier: str) -> str:
    """The area a mutant id belongs to, read from its path."""
    path = identifier.split("::", 1)[0]
    for prefix, area in (
        ("app/forms/", "forms"),
        ("app/core/", "core"),
        ("app/commands/", "commands"),
        ("app/migrations/data/", "migrations"),
    ):
        if path.startswith(prefix):
            return area
    return "other"


def plan(
    tree: Path, ids: Optional[Sequence[str]]
) -> Tuple[List[mutate.Mutant], List[str]]:
    """The mutants to run: the baseline's ids when given, else the seeded sample."""
    if ids:
        found, gone = [], []
        for identifier in ids:
            mutant = mutate.find(tree, identifier)
            if mutant is None:
                gone.append(identifier)
            else:
                found.append(mutant)
        return found, gone
    chosen: List[mutate.Mutant] = []
    for area, files in area_files(tree).items():
        chosen.extend(mutate.sample(tree, files, QUOTAS[area], SEED))
    return chosen, []


def covering_tests(
    data: Path, tree: Path, mutants: Sequence[mutate.Mutant], every: Sequence[str]
) -> Dict[str, List[str]]:
    """The tests that reached each mutant's lines; every test for import-time lines."""
    import coverage

    store = coverage.CoverageData(str(data))
    store.read()
    by_path: Dict[str, Dict[int, List[str]]] = {}
    tests: Dict[str, List[str]] = {}
    for mutant in mutants:
        if mutant.path not in by_path:
            by_path[mutant.path] = store.contexts_by_lineno(str(tree / mutant.path))
        lines = by_path[mutant.path]
        contexts = {
            context
            for line in range(mutant.line, mutant.end_line + 1)
            for context in lines.get(line, [])
        }
        found = sorted({context.rsplit("|", 1)[0] for context in contexts if context})
        tests[mutant.id] = found or (sorted(every) if "" in contexts else [])
    return tests


def _ordered(tests: Sequence[str], seconds: Dict[str, float]) -> List[str]:
    weight: Dict[str, float] = {}
    for nodeid in tests:
        path = nodeid.split("::", 1)[0]
        weight[path] = weight.get(path, 0.0) + seconds.get(nodeid, 1.0)
    return sorted(tests, key=lambda nodeid: weight[nodeid.split("::", 1)[0]])


def run_mutant(
    tree: Path,
    work: Path,
    mutant: mutate.Mutant,
    tests: Sequence[str],
    seconds: Dict[str, float],
) -> Dict[str, object]:
    """Apply a mutant to a worker copy, run the tests reaching it, restore the file."""
    if not tests:
        return {"status": "uncovered", "tests": 0}
    target = tree / mutant.path
    original = target.read_text(encoding="utf-8")
    ordered = _ordered(tests, seconds)
    budget = 60.0 + 3.0 * sum(seconds.get(nodeid, 1.0) for nodeid in ordered)
    target.write_text(mutate.apply(original, mutant), encoding="utf-8")
    try:
        run = pytest(
            tree,
            work,
            f"mutant-{tree.name}",
            [*_files(ordered, tree), "-x"],
            select=ordered,
            timeout=budget,
        )
    finally:
        target.write_text(original, encoding="utf-8")
    killer = run.failing()[:1]
    return {
        "status": _status(run),
        "exit": run.code,
        "tests": len(ordered),
        "seconds": round(run.seconds, 1),
        "killed_by": killer[0] if killer else None,
    }


def _status(run: Run) -> str:
    if run.timed_out:
        return "timeout"
    if run.code == 0:
        return "survived"
    if run.code in (1, 2, 3, 4):
        return "killed"
    return "error"


def run_mutants(
    source: Path,
    work: Path,
    mutants: Sequence[mutate.Mutant],
    tests: Dict[str, List[str]],
    seconds: Dict[str, float],
    workers: int,
) -> Dict[str, Dict[str, object]]:
    """Every mutant on a pool of worker copies, the costliest first."""
    copies: List[Path] = []
    for number in range(workers):
        copies.append(copy_tree(source, work / f"worker{number}"))
    free = list(copies)
    lock = threading.Lock()
    results: Dict[str, Dict[str, object]] = {}
    done = [0]

    def job(mutant: mutate.Mutant) -> None:
        with lock:
            tree = free.pop()
        try:
            result = run_mutant(tree, work, mutant, tests[mutant.id], seconds)
        finally:
            with lock:
                free.append(tree)
        with lock:
            results[mutant.id] = result
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == len(mutants):
                log(f"mutants: {done[0]}/{len(mutants)}")

    cost = {
        mutant.id: sum(seconds.get(nodeid, 1.0) for nodeid in tests[mutant.id])
        for mutant in mutants
    }
    ordered = sorted(mutants, key=lambda mutant: -cost[mutant.id])
    parallel([lambda mutant=mutant: job(mutant) for mutant in ordered], workers)
    return dict(sorted(results.items()))


def mutant_summary(
    results: Dict[str, Dict[str, object]], gone: Sequence[str]
) -> Dict[str, object]:
    """Killed, survived, timeout and uncovered per area, and every id's status."""
    areas: Dict[str, Dict[str, int]] = {}
    for identifier, result in results.items():
        counts = areas.setdefault(
            area_of(identifier),
            {"killed": 0, "timeout": 0, "survived": 0, "uncovered": 0, "error": 0},
        )
        counts[str(result["status"])] += 1
    return {
        "seed": SEED,
        "areas": dict(sorted(areas.items())),
        "status": {
            identifier: result["status"] for identifier, result in results.items()
        },
        "gone": list(gone),
    }


def change_job(
    source: Path, work: Path, change: Change
) -> Callable[[], Dict[str, object]]:
    """A job that applies one standard change to its own copy and runs the suite."""

    def job() -> Dict[str, object]:
        tree = copy_tree(source, work / f"change-{change.key}")
        target = tree / change.path
        text = target.read_text(encoding="utf-8")
        if text.count(change.old) != 1:
            return {"title": change.title, "applies": False}
        target.write_text(text.replace(change.old, change.new), encoding="utf-8")
        run = pytest(tree, work, f"change-{change.key}", list(SUITE))
        still, flaky = confirmed(tree, work, f"change-{change.key}", run)
        goldens = [nodeid for nodeid in still if nodeid.startswith(GOLDENS)]
        return {
            "title": change.title,
            "applies": True,
            "exit": run.code,
            "failing_tests": len(still),
            "failing_files": len({nodeid.split("::", 1)[0] for nodeid in still}),
            "goldens": len(goldens),
            "other_tests": len(still) - len(goldens),
            "caught_by": still if change.key == CAUGHT else [],
            "flaky": flaky,
        }

    return job


def work_dir(requested: Optional[str]) -> Path:
    """The folder that holds the copies: the one asked for, or a fresh temporary one."""
    if requested:
        path = Path(requested)
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()
    return Path(tempfile.mkdtemp(prefix="testguard-")).resolve()

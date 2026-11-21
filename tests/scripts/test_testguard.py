"""The test guard measures the suite, so its own logic is tested here.

The mutator must address a mutant by function and statement, so the same set still
applies after the tests change, and must change one expression and nothing else. The
ledger must refuse an entry whose test is gone, since it is what keeps the past bugs
pinned through a rebuild of the suite. The verdict must call a lost line, a lost kill
or an uncaught validator unsafe, except a kill `accepted.yml` shows was false, which
it reports as accepted.
"""

import ast
import os
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.keiko.testguard import compare, ledger, measure, mutate  # noqa: E402

pytestmark = pytest.mark.unit


SOURCE = textwrap.dedent(
    '''\
    """A module."""
    import logging

    logger = logging.getLogger(__name__)


    def check(value: int = 3) -> bool:
        """Says whether the value is big."""
        logger.info("checking %s", value)
        if value == 1:
            notify("one")
        label = f"value {value}"
        return value > 2 if label else True


    class Box:
        def size(self, items):
            def inner():
                return len(items)
            return inner() + 1
    '''
)


def _by(found, operator):
    return [mutant for mutant in found if mutant.operator == operator]


def _mutated_line(mutant, source=SOURCE):
    return mutate.apply(source, mutant).splitlines()[mutant.line - 1].strip()


def test_every_operator_changes_one_expression_of_a_function_body():
    found = mutate.mutants(SOURCE, "app/sample.py")

    assert _mutated_line(_by(found, "compare")[0]) == "if (value != 1):"
    assert _mutated_line(_by(found, "negate")[0]) == "if (not (value == 1)):"
    assert _mutated_line(_by(found, "drop-call")[0]) == "pass"
    assert _mutated_line(_by(found, "return-none")[0]) == "return None"
    assert (
        _mutated_line(_by(found, "bool")[0]) == "return value > 2 if label else (False)"
    )
    constants = [_mutated_line(mutant) for mutant in _by(found, "constant")]
    assert "if value == (2):" in constants
    assert "notify(('XXoneXX'))" in constants


def test_docstrings_log_calls_fstrings_and_signatures_are_never_mutated():
    lines = {mutant.line for mutant in mutate.mutants(SOURCE, "app/sample.py")}
    source_lines = SOURCE.splitlines()

    untouched = [
        number
        for number, text in enumerate(source_lines, start=1)
        if "Says whether" in text
        or "logger.info" in text
        or 'f"value' in text
        or text.startswith("def check")
    ]
    assert untouched and not lines & set(untouched)


def test_a_mutant_is_addressed_by_function_and_statement_not_by_line():
    moved = SOURCE.replace("logger = logging", "# a comment\n\n\nlogger = logging")

    before = {mutant.id for mutant in mutate.mutants(SOURCE, "app/sample.py")}
    after = {mutant.id for mutant in mutate.mutants(moved, "app/sample.py")}

    assert before == after
    assert "app/sample.py::check::2.body0::drop-call#0" in before
    assert "app/sample.py::Box.size.inner::0::return-none#0" in before
    assert "app/sample.py::Box.size::1::constant#0" in before


def test_a_mutant_keeps_every_other_byte_and_the_line_count():
    source = textwrap.dedent(
        """\
        def pick(name):
            texto = "ação" if name == "é" else "não"
            return call(
                name,
                "fim",
            )
        """
    )
    for mutant in mutate.mutants(source, "app/pick.py"):
        mutated = mutate.apply(source, mutant)
        ast.parse(mutated)
        assert len(mutated.splitlines()) == len(source.splitlines())
        assert mutated.count("\n") == source.count("\n")

    flip = _by(mutate.mutants(source, "app/pick.py"), "compare")[0]
    assert _mutated_line(flip, source) == 'texto = "ação" if (name != \'é\') else "não"'


def test_the_sample_is_seeded_and_takes_every_operator_in_turn(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/sample.py").write_text(SOURCE, encoding="utf-8")

    first = mutate.sample(tmp_path, ["app/sample.py"], 6, seed=1)
    again = mutate.sample(tmp_path, ["app/sample.py"], 6, seed=1)

    assert [mutant.id for mutant in first] == [mutant.id for mutant in again]
    assert {mutant.operator for mutant in first} == set(mutate.OPERATORS)


def test_an_id_whose_statement_is_gone_no_longer_applies(tmp_path):
    (tmp_path / "app").mkdir()
    path = tmp_path / "app/sample.py"
    path.write_text(SOURCE, encoding="utf-8")
    identifier = "app/sample.py::check::2.body0::drop-call#0"

    assert mutate.find(tmp_path, identifier).operator == "drop-call"

    path.write_text(SOURCE.replace('notify("one")', "pass"), encoding="utf-8")

    assert mutate.find(tmp_path, identifier) is None


def _suite(root, files):
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


PINNED = {
    "tests/test_pinned.py": '''\
        def test_the_week_has_seven_days():
            """Broke as: the window summed eight days.

            Guaranteed: the digest counts seven days."""

        class TestBox:
            def test_a_box_closes(self):
                """Broke as: the box stayed open."""
        ''',
    "tests/test_module.py": '''\
        """A server keeps one time zone.

        Broke as: every reminder moved when the zone changed."""

        def test_one():
            pass
        ''',
}


def test_the_scan_finds_every_broke_as_and_the_guarantee_it_records(tmp_path):
    _suite(tmp_path, PINNED)

    pins = {pin.test: pin for pin in ledger.scan(tmp_path)}

    assert set(pins) == {
        "tests/test_module.py",
        "tests/test_pinned.py::test_the_week_has_seven_days",
        "tests/test_pinned.py::TestBox::test_a_box_closes",
    }
    week = pins["tests/test_pinned.py::test_the_week_has_seven_days"]
    assert week.broke == "the window summed eight days."
    assert week.guarantee == "the digest counts seven days."
    assert pins["tests/test_module.py"].guarantee == "A server keeps one time zone."
    assert (
        pins["tests/test_pinned.py::TestBox::test_a_box_closes"].guarantee
        == "a box closes"
    )


def test_an_entry_whose_test_is_gone_fails_the_check(tmp_path):
    _suite(tmp_path, PINNED)
    book = tmp_path / "ledger.yml"
    ledger.sync(tmp_path, book)

    assert ledger.check(tmp_path, ledger.load(book)).ok

    (tmp_path / "tests/test_module.py").unlink()
    found = ledger.check(tmp_path, ledger.load(book))

    assert not found.ok
    assert [entry["test"] for entry in found.missing] == ["tests/test_module.py"]


def test_a_moved_test_is_pinned_once_its_entry_names_the_new_home(tmp_path):
    _suite(tmp_path, PINNED)
    book = tmp_path / "ledger.yml"
    ledger.sync(tmp_path, book)
    _suite(
        tmp_path,
        {
            "tests/test_pinned.py": '''\
            def test_the_digest_window():
                """Broke as: the window summed eight days, and the box stayed open."""
            ''',
        },
    )
    stale = ledger.check(tmp_path, ledger.load(book))
    assert len(stale.missing) == 2

    entries = ledger.load(book)
    for entry in entries:
        if entry["test"].startswith("tests/test_pinned.py"):
            entry["test"] = "tests/test_pinned.py::test_the_digest_window"
    ledger.write(entries, book)

    assert ledger.check(tmp_path, ledger.load(book)).ok


def test_sync_adds_new_bugs_and_keeps_every_entry(tmp_path):
    _suite(tmp_path, PINNED)
    book = tmp_path / "ledger.yml"
    ledger.sync(tmp_path, book)
    _suite(
        tmp_path,
        {
            "tests/test_new.py": '''\
            def test_a_new_bug():
                """Broke as: something new."""
            ''',
        },
    )

    added = ledger.sync(tmp_path, book)

    assert [entry["id"] for entry in added] == ["pb-004"]
    assert [entry["id"] for entry in ledger.load(book)] == [
        "pb-001",
        "pb-002",
        "pb-003",
        "pb-004",
    ]
    assert ledger.sync(tmp_path, book) == []


def test_an_entry_reads_the_outcome_of_every_instance_of_its_test():
    results = {
        "tests/test_a.py::test_x[one]": {"outcome": "passed"},
        "tests/test_a.py::test_x[two]": {"outcome": "failed"},
        "tests/test_a.py::test_xy": {"outcome": "passed"},
        "tests/test_b.py::test_y": {"outcome": "skipped"},
    }

    assert ledger.outcome({"test": "tests/test_a.py::test_x"}, results) == "failed"
    assert ledger.outcome({"test": "tests/test_a.py::test_xy"}, results) == "passed"
    assert ledger.outcome({"test": "tests/test_b.py"}, results) == "skipped"
    assert ledger.outcome({"test": "tests/test_c.py"}, results) == "not run"


def test_every_past_bug_in_the_ledger_still_has_its_test():
    entries = ledger.load()
    found = ledger.check(Path(ROOT), entries)

    assert len(entries) >= 161
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert found.missing == [], (
        "these past bugs lost the test that pinned them; point each entry of "
        "scripts/keiko/testguard/ledger.yml at the test that pins it now: "
        f"{[(entry['id'], entry['test']) for entry in found.missing]}"
    )


def test_line_ranges_round_trip():
    assert measure.ranges([1, 2, 3, 5, 7, 8]) == "1-3,5,7-8"
    assert measure.lines_of("1-3,5,7-8") == [1, 2, 3, 5, 7, 8]
    assert measure.lines_of("") == []


def _measurement(missing="", status="killed", caught=4):
    module = {
        "statements": 10,
        "covered": 10,
        "branches": 2,
        "covered_branches": 2,
        "missing": missing,
    }
    return {
        "suite": {"exit": 0, "tests": 10, "failing": [], "flaky": []},
        "ledger": {
            "entries": 1,
            "missing": [],
            "unledgered": [],
            "history_dropped": [],
            "failed": [],
        },
        "coverage": {"modules": {"app/x.py": module}},
        "mutants": {"status": {"app/x.py::f::0::bool#0": status}, "gone": []},
        "changes": {"validator": {"applies": True, "exit": 1, "failing_tests": caught}},
    }


def test_the_verdict_is_safe_when_nothing_was_lost():
    assert compare.compare(_measurement(), _measurement())["safe"]


@pytest.mark.parametrize(
    "now",
    [
        _measurement(missing="4-5"),
        _measurement(status="survived"),
        _measurement(caught=0),
    ],
    ids=["a line lost", "a kill lost", "the validator uncaught"],
)
def test_the_verdict_is_unsafe_when_the_suite_lost_a_guarantee(now):
    found = compare.compare(now, _measurement())

    assert not found["safe"]
    assert len(found["reasons"]) == 1


def test_a_lost_kill_shown_false_is_reported_as_accepted_not_as_a_loss():
    accepted = {
        "app/x.py::f::0::bool#0": {
            "id": "app/x.py::f::0::bool#0",
            "reason": "the kill came from a test that failed with no mutant",
            "evidence": "pytest tests/test_x.py::test_a tests/test_x.py::test_b",
        }
    }

    found = compare.compare(_measurement(status="survived"), _measurement(), accepted)

    assert found["safe"] and found["lost_kills"] == []
    assert found["accepted"] == {"app/x.py::f::0::bool#0": "survived"}
    assert not compare.compare(_measurement(status="survived"), _measurement())["safe"]


def test_an_accepted_mutant_without_its_reason_or_evidence_is_refused(tmp_path):
    book = tmp_path / "accepted.yml"
    book.write_text(
        "mutants:\n- id: app/x.py::f::0::bool#0\n  reason: a test left state\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="has no evidence"):
        compare.load_accepted(book)


def test_every_accepted_mutant_was_a_baseline_kill_with_its_evidence():
    accepted = compare.load_accepted()
    status = compare.load_baseline()["mutants"]["status"]

    assert {identifier: status.get(identifier) for identifier in accepted} == {
        identifier: "killed" for identifier in accepted
    }, "accept only a kill the baseline recorded and that was shown false"


def _module(missing, unstable=None):
    module = {
        "statements": 10,
        "covered": 10 - len(measure.lines_of(missing)),
        "branches": 2,
        "covered_branches": 2,
        "missing": missing,
    }
    if unstable:
        module["unstable"] = unstable
    return module


def test_two_passes_cover_what_either_ran_and_name_the_lines_that_flipped():
    first = {"modules": {"app/x.py": _module("3-4,8")}}
    second = {"modules": {"app/x.py": _module("4,8-9")}}

    both = measure.merged(first, second)["modules"]["app/x.py"]

    assert both["missing"] == "4,8"
    assert both["covered"] == 8
    assert both["unstable"] == "3,9"


def test_a_line_the_baseline_saw_flip_is_not_counted_as_lost():
    before = {"app/x.py": _module("", unstable="5")}
    now = {"app/x.py": _module("5-6")}

    lost = compare.lost_lines(now, before)

    assert lost["lines"] == {"app/x.py": [6]}
    assert compare.lost_count(lost) == 1


def test_a_new_baseline_keeps_the_unstable_lines_of_the_same_app():
    previous = {
        "app_tree": "abc",
        "coverage": {"modules": {"app/x.py": _module("", "5")}},
    }
    current = {
        "app_tree": "abc",
        "coverage": {"modules": {"app/x.py": _module("", "7")}},
    }

    compare.carry_unstable(current, previous)

    assert current["coverage"]["modules"]["app/x.py"]["unstable"] == "5,7"

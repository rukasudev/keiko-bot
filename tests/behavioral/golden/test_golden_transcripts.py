"""Golden transcripts: every canonical admin path of every form, byte for byte.

These are the UX contract of the form engine (review II.3). Record them with
`pytest tests/behavioral/golden --update-golden` only when
`docs/ux-changes.md` lists the change that made them move.
"""
import re
from pathlib import Path

import pytest

from tests.behavioral.golden.paths import all_paths

UX_CHANGES = Path(__file__).resolve().parents[3] / "docs" / "ux-changes.md"

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

CASES = [
    pytest.param(path, locale, id=f"{path.id}.{locale}")
    for path in all_paths()
    for locale in path.locales
]


@pytest.mark.parametrize("path,locale", CASES)
async def test_golden_transcript(path, locale, scenario_factory, deps, golden):
    scenario = await path.run(scenario_factory, deps, locale)
    await scenario.finish()
    golden.check(scenario, path.form, path.name, locale)


def test_every_ux_change_has_an_id_of_its_own():
    """`docs/ux-changes.md` merges as a union, so two branches that each take the
    next id would merge into one id meaning two changes, and a golden's `allowed=`
    would name the wrong one."""
    rows = UX_CHANGES.read_text(encoding="utf-8")
    ids = re.findall(r"^\| (ux-\d+) \|", rows, flags=re.MULTILINE)

    assert ids, "the log has rows"
    assert sorted({change for change in ids if ids.count(change) > 1}) == []

"""Golden books: every canonical admin path of every form, one book per form.

These are the UX contract of the form engine (review II.3). Each form keeps one
`tests/behavioral/golden/<form>.yml`: every screen once, every text written as
the copy key that wrote it, and the words of the screens its paths module names
in `golden_book(FORM, rendered=...)` in both locales. Record them with
`pytest tests/behavioral/golden --update-golden`, once per change; a change to what
an admin sees is listed in `docs/ux-changes.md` first.
"""
import re
from pathlib import Path

import pytest

from tests.behavioral.golden.paths import BOOKS, all_paths, flow_ids
from tests.behavioral.harness.copy_keys import CopyTrace

UX_CHANGES = Path(__file__).resolve().parents[3] / "docs" / "ux-changes.md"

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

CASES = [
    pytest.param(path, locale, id=f"{path.id}.{locale}")
    for path in all_paths()
    for locale in path.locales
]


@pytest.mark.parametrize("path,locale", CASES)
async def test_golden_transcript(path, locale, scenario_factory, deps, golden,
                                 monkeypatch):
    book = BOOKS[path.form]
    trace = CopyTrace().install(monkeypatch)
    scenario = await path.run(scenario_factory, deps, locale)
    await scenario.finish()
    golden.check_book(scenario, trace, book, path.name, locale, flow_ids(path.form))


def test_every_form_keeps_the_words_of_its_panel_and_its_review():
    """A book written only as keys passes a broken fill or a bad translation, so
    every form with paths declares its book and names the two screens, its panel
    and its review, whose words the book keeps in both locales."""
    forms = {path.form for path in all_paths()}

    assert sorted(forms - set(BOOKS)) == []
    assert sorted(form for form in forms if len(BOOKS[form].rendered) != 2) == []


def test_every_ux_change_has_an_id_of_its_own():
    """`docs/ux-changes.md` merges as a union, so two branches that each take the
    next id would merge into one id meaning two changes, and a book re-recorded
    for one would cite the wrong one."""
    rows = UX_CHANGES.read_text(encoding="utf-8")
    ids = re.findall(r"^\| (ux-\d+) \|", rows, flags=re.MULTILINE)

    assert ids, "the log has rows"
    assert sorted({change for change in ids if ids.count(change) > 1}) == []

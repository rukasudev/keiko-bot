"""Golden transcripts: every canonical admin path of every form, byte for byte.

These are the UX contract of the form engine (review II.3). Record them with
`pytest tests/behavioral/golden --update-golden` only when
`docs/ux-changes.md` lists the change that made them move.
"""
import pytest

from tests.behavioral.golden.paths import all_paths

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

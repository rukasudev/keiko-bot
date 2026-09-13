"""Fixtures for the behavioral suite.

Builds on top of tests/conftest.py (real i18n, early-patched Mongo/Redis,
autouse dependency injection). Adds: repo-root CWD guard (the form YAML
loader resolves relative to CWD), a FormScenario factory, a two-locale
parametrized fixture, and the golden transcript recorder/checker.
"""
import os
from pathlib import Path

import pytest

from tests.behavioral.harness import golden as golden_module
from tests.behavioral.harness.driver import FormScenario
from tests.mocks.discord import create_guild, create_member

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLDEN_ROOT = Path(__file__).resolve().parent / "golden"


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden", action="store_true", default=False,
        help="rewrite the golden transcripts from the engine under test",
    )
    parser.addoption(
        "--engine", action="store", default="legacy", choices=("legacy", "v2"),
        help="which form engine the scenarios drive",
    )


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def engine(request) -> str:
    """The form engine under test: `legacy` unless `--engine v2`."""
    return request.config.getoption("--engine", default="legacy")


@pytest.fixture
def scenario_factory(deps, engine):
    """Build FormScenario instances bound to a fresh mock guild + Mongo."""

    def factory(locale: str = "pt-br", guild=None, user=None) -> FormScenario:
        guild = guild or create_guild()
        user = user or create_member(guild, id=555, name="Tester")
        return FormScenario(guild=guild, user=user, locale=locale,
                            mongo=deps.mongo_client, engine=engine)

    return factory


@pytest.fixture(params=["pt-br", "en-us"])
def each_locale(request):
    return request.param


class GoldenCheck:
    """Resolve `tests/behavioral/golden/<form>/<scenario>.<locale>.json` and
    either record it (`--update-golden`) or assert the scenario matches it."""

    def __init__(self, update: bool):
        self.update = update

    @staticmethod
    def path(form: str, scenario: str, locale: str) -> Path:
        return GOLDEN_ROOT / form / f"{scenario}.{locale}.json"

    def check(self, scenario: FormScenario, form: str, name: str, locale: str,
              allowed=()) -> Path:
        path = self.path(form, name, locale)
        if self.update:
            golden_module.record(scenario, path)
        else:
            golden_module.assert_golden(scenario, path, allowed=allowed)
        return path


@pytest.fixture
def golden(request) -> GoldenCheck:
    return GoldenCheck(update=request.config.getoption("--update-golden", default=False))

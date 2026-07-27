"""Fixtures for the behavioral suite.

Builds on top of tests/conftest.py (real i18n, early-patched Mongo/Redis,
autouse dependency injection). Adds: repo-root CWD guard (the form YAML
loader resolves relative to CWD), a FormScenario factory, and a two-locale
parametrized fixture.
"""
import os

import pytest

from tests.behavioral.harness.driver import FormScenario
from tests.mocks.discord import create_guild, create_member

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def scenario_factory(deps):
    """Build FormScenario instances bound to a fresh mock guild + Mongo."""

    def factory(locale: str = "pt-br", guild=None, user=None) -> FormScenario:
        guild = guild or create_guild()
        user = user or create_member(guild, id=555, name="Tester")
        return FormScenario(guild=guild, user=user, locale=locale,
                            mongo=deps.mongo_client)

    return factory


@pytest.fixture(params=["pt-br", "en-us"])
def each_locale(request):
    return request.param

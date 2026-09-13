"""Both engines leave the same documents behind, path by path.

The goldens prove what the admin sees; this proves what the database keeps:
every golden path is run on the old engine and on the platform against a
fresh store, and the collections must match once ids and clocks are dropped.
"""
import pytest

from app.constants import Commands
from app.forms.adapters.discord.entrypoints import RUNTIME
from app.services.block_links import normalize_block_links_config
from tests.behavioral.golden.paths import all_paths
from tests.behavioral.harness.driver import FormScenario
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

CLOCK_FIELDS = ("_id", "created_at", "updated_at", "datetime", "date")
CASES = [pytest.param(path, id=path.id) for path in all_paths()]


def _factory(deps, engine):
    def factory(locale="pt-br", guild=None, user=None):
        guild = guild or create_guild()
        user = user or create_member(guild, id=555, name="Tester")
        return FormScenario(guild=guild, user=user, locale=locale,
                            mongo=deps.mongo_client, engine=engine)

    return factory


def _without_titles(value):
    """Item entries drop their `title`: presentation the old engine rewrites from
    the YAML on every write and the platform reads from the definition."""
    if isinstance(value, dict):
        return {k: _without_titles(v) for k, v in value.items() if k != "title"}
    if isinstance(value, list):
        return [_without_titles(item) for item in value]
    return value


def _stable(document, name):
    if name == "block_links":
        document = normalize_block_links_config(document)
    return {
        key: _without_titles(value)
        for key, value in document.items()
        if key not in CLOCK_FIELDS
    }


def snapshot(mongo):
    """Every document of every collection, without ids and clocks.

    block_links documents are compared as every reader sees them, after the
    read-time normalization (the old engine also writes that shape back on
    an item change, from its in-memory snapshot).
    """
    collections = {}
    for db_name, database in mongo._databases.items():
        for name, collection in database._collections.items():
            documents = [_stable(document, name) for document in collection._data]
            if documents:
                collections[f"{db_name}.{name}"] = documents
    return collections


def _fresh(deps):
    deps.mongo_client._databases.clear()
    deps.redis_client.flushdb()
    RUNTIME.reset()


@pytest.mark.parametrize("path", CASES)
async def test_both_engines_persist_the_same_documents(path, deps, monkeypatch):
    _fresh(deps)
    monkeypatch.setattr(Commands, "FORM_ENGINE_V2_KEYS", frozenset())
    legacy = await path.run(_factory(deps, "legacy"), deps, "pt-br")
    await legacy.finish()
    expected = snapshot(deps.mongo_client)
    monkeypatch.undo()

    _fresh(deps)
    platform = await path.run(_factory(deps, "v2"), deps, "pt-br")
    await platform.finish()
    actual = snapshot(deps.mongo_client)

    assert actual == expected

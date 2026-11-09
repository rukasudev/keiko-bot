"""No language block is read as a plural form, so every key under it resolves.

Broke as: the /configurar health line showed
`commands.commands.setup.health.delivered.block-links.many` instead of "✅ Bloqueei 3
links nos últimos 7 dias". python-i18n stores a block with two or more of the keys
`zero`, `one`, `few`, `many`, `other` as one plural value, so `ml("....one")` finds
nothing and returns its key. Shared behaviour: every language file, read through
`ml`. Consumer that exposed it: the health line of a /configurar category page,
which now closes each panel's statistics, its counts under
`commands.commands.commons.stats.recent.delivered-one` and `delivered-many`.
Guaranteed: no block in `app/languages` has two plural-named keys.
"""
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

LANGUAGES = Path(__file__).resolve().parents[3] / "app" / "languages"
PLURAL_FORMS = {"zero", "one", "few", "many", "other"}


def _plural_blocks(node, path=""):
    if not isinstance(node, dict):
        return
    if len(PLURAL_FORMS & {str(key) for key in node}) >= 2:
        yield path
    for key, value in node.items():
        yield from _plural_blocks(value, f"{path}.{key}")


@pytest.mark.parametrize(
    "path", sorted(LANGUAGES.glob("**/*.yml")), ids=lambda path: path.name
)
def test_no_language_block_has_two_plural_named_keys(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert list(_plural_blocks(document)) == []

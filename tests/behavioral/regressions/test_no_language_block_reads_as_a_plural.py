"""No language block is read as a plural form, so every key under it resolves.

Broke as: the /configurar health line showed
`commands.commands.setup.health.delivered.block-links.many` instead of "✅ Bloqueei 3
links nos últimos 7 dias". python-i18n's file loader stored a block with two or more
of the keys `zero`, `one`, `few`, `many`, `other` as one plural value, so
`ml("....one")` found nothing and returned its key. Shared behaviour: every language
file, read through `ml`. Consumer that exposed it: the health line of a /configurar
category page, which now closes each panel's statistics, its counts under each
command's `command.stats.delivered`, a `{one, other}` block.
Guaranteed: every text of a copy file resolves through `ml` to its own words in each
locale, the keys of a `{one, other}` block included, since the copy loader adds each
text under its full key; and no block of a `form.yml` has two plural-named keys.
"""

import pytest
import yaml

from app.core import i18n
from app.core.commands import form_files
from app.core.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

PLURAL_FORMS = {"zero", "one", "few", "many", "other"}


def _plural_blocks(node, path=""):
    if not isinstance(node, dict):
        return
    if len(PLURAL_FORMS & {str(key) for key in node}) >= 2:
        yield path
    for key, value in node.items():
        yield from _plural_blocks(value, f"{path}.{key}")


@pytest.mark.parametrize(
    "path,prefix",
    [
        pytest.param(path, prefix, id=f"{path.parent.name}/{path.name}")
        for path, prefix in i18n.files()
    ],
)
def test_every_text_of_a_copy_file_resolves_to_its_own_words(path, prefix):
    unresolved = [
        f"{key} ({locale})"
        for key, words in i18n.written(path, prefix)
        for locale in i18n.locales()
        if ml(key, locale) != words[locale]
    ]

    assert unresolved == []


def test_a_plural_block_of_the_copy_resolves_each_of_its_forms():
    document = yaml.safe_load(
        (i18n.APP / "commands/block_links/copy.yml").read_text(encoding="utf-8")
    )

    assert list(_plural_blocks(document)) == [".command.stats.delivered"]
    assert ml("block_links.command.stats.delivered.one", "pt-br").startswith("✅ Bloqueei 1")
    assert "$count links" in ml("block_links.command.stats.delivered.other", "en-us")


@pytest.mark.parametrize(
    "path",
    sorted(form_files().values()),
    ids=lambda path: f"{path.parent.name}/{path.name}",
)
def test_no_form_block_has_two_plural_named_keys(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert list(_plural_blocks(document)) == []

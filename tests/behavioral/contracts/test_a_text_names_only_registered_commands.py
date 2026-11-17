"""Contract: every slash command a text names is one Discord registers, by the path a
member types in the text's language.

Incident: the default roles error told the admin to run `/default roles` (pt-br
`/cargos padrão`), the last two names of `/moderations default roles` typed in; the
copy loaded, every key resolved, and the command it named did not exist. A text that
names a command writes its context entry (`{context:commands.<key>}`,
`{context:groups.<group>}`, code-style rule 28), which the loader fills from the same
copy the translator sends Discord; this contract checks what each text ends up
saying, written either way, against the tree the bot syncs.

Swept: every shared file of `app/languages`, every command's `copy.yml` and
`form.yml`, and `app/forms/vocabulary.yml`, each read as the bot reads it, its known
context written in. A command in backticks that starts with `/` is checked; one the
code completes at render (`/$command_name`, `/{context:command}`) is left to the code
that fills it.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core import i18n
from app.core.commands import REPOSITORY, form_files, strict_yaml
from app.core.facts import with_context
from tests.helpers.slash_tree import DISCORD_LOCALES, synced_tree

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("command_copy")]

VOCABULARY = REPOSITORY / "app" / "forms" / "vocabulary.yml"
NAMED = re.compile(r"`(/[^`]*)`")


def _loaded(path: Path, name: str) -> Any:
    raw = strict_yaml(path.read_text(encoding="utf-8"), str(path))
    return with_context(raw, name)


def _documents() -> Iterator[tuple[Path, Any]]:
    for path, _prefix in i18n.files():
        yield path, i18n.read(path)
    for key, path in sorted(form_files().items()):
        yield path, _loaded(path, key)
    yield VOCABULARY, _loaded(VOCABULARY, str(VOCABULARY))


def _texts(node: Any, where: str) -> Iterator[tuple[str, dict[str, str]]]:
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _texts(item, f"{where}[{index}]")
    elif isinstance(node, dict):
        if node and set(node) <= set(DISCORD_LOCALES):
            yield where, node
            return
        for key, value in node.items():
            yield from _texts(value, f"{where}.{key}" if where else str(key))


def _named(words: str) -> list[str]:
    return [
        command
        for command in NAMED.findall(words)
        if command != "/" and "$" not in command and "{" not in command
    ]


def _commands_named(locale: str) -> list[tuple[str, str, str]]:
    return [
        (str(path.relative_to(REPOSITORY)), where, command)
        for path, document in _documents()
        for where, text in _texts(document, "")
        for command in _named(str(text.get(locale) or ""))
    ]


@pytest.fixture
async def tree():
    return await synced_tree()


@pytest.mark.parametrize("locale", list(DISCORD_LOCALES))
async def test_every_slash_command_a_text_names_is_one_discord_registers(tree, locale):
    typed = tree.typed(locale)

    unknown = [named for named in _commands_named(locale) if named[2] not in typed]

    assert not unknown, (
        f"a text names a slash command Discord does not register in {locale}; write "
        f"it as its {{context:commands.<key>}}: {unknown}"
    )


@pytest.mark.parametrize("locale", list(DISCORD_LOCALES))
def test_the_sweep_reads_every_kind_of_copy(locale):
    files = {Path(path) for path, _where, _command in _commands_named(locale)}

    assert {path.name for path in files} >= {"copy.yml", "form.yml", "vocabulary.yml"}
    assert any(path.parent.name == "languages" for path in files)

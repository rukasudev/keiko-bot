"""The copy loader: shared files plus each command's `copy.yml`, every text
bilingual."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import i18n as python_i18n
import pytest
import yaml

from app.core import commands as command_registry
from app.core import i18n
from app.core.commands import Text
from app.ui.embed import Response
from tests.helpers.copy import SAMPLE

LOCALES = i18n.locales()
OK = "ok:\n  en-us: Ok\n  pt-br: Ok\n"
Files = Callable[..., None]
COMMAND = "app/commands/one/copy.yml"


@pytest.fixture
def copy_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Files]:
    """Point the loader at copy files written under a temporary app folder."""
    monkeypatch.setattr(i18n, "APP", tmp_path / "app")

    def use(*files: tuple[str, str, str]) -> None:
        written = []
        for relative, prefix, text in files:
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append((path, prefix))
        monkeypatch.setattr(i18n, "files", lambda: written)
        i18n.typed.cache_clear()

    yield use
    monkeypatch.undo()
    i18n.typed.cache_clear()
    i18n.typed()


@pytest.mark.parametrize("locale", LOCALES)
def test_no_key_is_defined_twice_in_the_real_copy(locale: str) -> None:
    assert i18n.collect(locale)


def test_a_command_copy_loads_under_its_key(copy_files: Files) -> None:
    text = "messages:\n  hint:\n    en-us: Hint\n    pt-br: Dica\n"
    copy_files((COMMAND, "one", text))

    assert i18n.collect("pt-br") == {"one.messages.hint": "Dica"}
    assert i18n.collect("en-us") == {"one.messages.hint": "Hint"}


def test_a_key_defined_in_two_files_names_both(copy_files: Files) -> None:
    copy_files(
        ("app/languages/messages.yml", "messages", OK),
        (COMMAND, "messages", OK),
    )

    with pytest.raises(i18n.CopyError) as error:
        i18n.collect("en-us")

    assert "app/languages/messages.yml" in str(error.value)
    assert COMMAND in str(error.value)


def test_a_key_in_one_file_and_a_block_in_another_names_both(
    copy_files: Files,
) -> None:
    block = "ok:\n  label:\n    en-us: Ok\n    pt-br: Ok\n"
    copy_files(
        ("app/languages/messages.yml", "messages", OK),
        (COMMAND, "messages", block),
    )

    with pytest.raises(i18n.CopyError) as error:
        i18n.collect("en-us")

    assert "messages.ok" in str(error.value)
    assert "app/languages/messages.yml" in str(error.value)
    assert COMMAND in str(error.value)


def test_a_key_written_twice_in_one_file_is_refused(copy_files: Files) -> None:
    copy_files((COMMAND, "one", OK + OK))

    with pytest.raises(i18n.CopyError, match="ok twice"):
        i18n.collect("en-us")


def test_a_text_missing_a_locale_is_refused(copy_files: Files) -> None:
    copy_files((COMMAND, "one", "ok:\n  en-us: Ok\n"))

    with pytest.raises(i18n.CopyError, match="one.ok: no pt-br"):
        i18n.collect("en-us")


def test_a_text_in_a_locale_keiko_does_not_speak_is_refused(copy_files: Files) -> None:
    copy_files((COMMAND, "one", OK + "  es-es: Vale\n"))

    with pytest.raises(i18n.CopyError, match="one.ok: es-es is no locale"):
        i18n.collect("en-us")


def test_a_text_written_with_no_locale_is_refused(copy_files: Files) -> None:
    copy_files((COMMAND, "one", "ok: Ok\n"))

    with pytest.raises(i18n.CopyError, match="one.ok is a text with no locale"):
        i18n.collect("en-us")


def test_a_text_yaml_reads_as_no_string_is_refused(copy_files: Files) -> None:
    text = "ok:\n  en-us: yes\n  pt-br: Sim\n"
    copy_files((COMMAND, "one", text))

    with pytest.raises(i18n.CopyError, match="the en-us text is no string"):
        i18n.collect("en-us")


@pytest.mark.parametrize(
    "text",
    [
        "ok: &shared\n  en-us: Ok\n  pt-br: Ok\n",
        "ok:\n  en-us: &word Ok\n  pt-br: *word\n",
    ],
    ids=["anchor", "alias"],
)
def test_an_anchor_or_an_alias_is_refused(copy_files: Files, text: str) -> None:
    copy_files((COMMAND, "one", text))

    with pytest.raises(i18n.CopyError, match="is an anchor or an alias"):
        i18n.collect("en-us")


def test_a_command_loads_its_copy_yml_and_never_its_form_yml() -> None:
    owned = [path for path, _ in i18n.files() if path.parent.parent.name == "commands"]

    assert owned and sorted(owned) == sorted(command_registry.copy_files().values())
    assert not set(owned) & set(command_registry.form_files().values())


def test_every_copy_file_parses_into_bilingual_texts() -> None:
    found = i18n.files()

    assert [path for path, _ in found if path.name == "copy.yml"]
    for path, prefix in found:
        assert i18n.written(path, prefix), path


def _one_locale_texts(node: Any, where: str = "") -> list[str]:
    if isinstance(node, list):
        return [
            found
            for index, item in enumerate(node)
            for found in _one_locale_texts(item, f"{where}[{index}]")
        ]
    if not isinstance(node, dict):
        return []
    named = {str(key) for key in node if i18n.LOCALE_NAME.fullmatch(str(key))}
    if named and named != set(LOCALES):
        return [f"{where}: {sorted(named)}"]
    return [
        found
        for key, value in node.items()
        for found in _one_locale_texts(value, f"{where}.{key}")
    ]


@pytest.mark.parametrize(
    "path",
    sorted(
        [
            *command_registry.copy_files().values(),
            *command_registry.form_files().values(),
        ]
    ),
    ids=lambda path: f"{path.parent.name}/{path.name}",
)
def test_every_text_of_a_command_is_written_in_both_locales(path: Path) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert _one_locale_texts(document) == []


@pytest.mark.parametrize("locale", LOCALES)
def test_every_copy_file_is_loaded_with_its_values(locale: str) -> None:
    loaded = i18n.keys(locale)
    missing: list[Any] = []
    for path, prefix in i18n.files():
        for key, words in i18n.written(path, prefix):
            if loaded.get(key) != words[locale]:
                missing.append((path.name, key))

    assert missing == []
    assert python_i18n.load_path == []


def test_every_copy_file_is_read_against_its_schema() -> None:
    prefixes = {prefix for _path, prefix in i18n.files()}

    assert {"locales", "block_links"} <= prefixes
    assert set(i18n.typed()) == prefixes


def test_a_copy_loads_its_texts_and_its_typed_blocks(copy_files: Files) -> None:
    copy_files((COMMAND, "one", SAMPLE))

    flat = i18n.collect("pt-br")
    response = i18n.block("one.responses.quiet", Response)

    assert flat["one.responses.saved.description"] == "Salvei $item."
    assert "one.responses.quiet.footer" not in flat, "a null text loads no key"
    assert response is not None and response.footer is None
    assert "footer" in response.model_fields_set


def test_a_typed_block_is_found_by_its_key_or_refused(copy_files: Files) -> None:
    copy_files((COMMAND, "one", SAMPLE))

    assert i18n.block("one.buttons.sync.label", Text) == Text.model_validate(
        {"en-us": "Sync", "pt-br": "Sincronizar"}
    )
    assert i18n.block("one.responses.missing", Response, required=False) is None
    with pytest.raises(i18n.CopyError, match="no copy file writes it"):
        i18n.block("one.responses.missing", Response)
    with pytest.raises(i18n.CopyError, match="not a Response"):
        i18n.block("one.errors.not_found", Response)


@pytest.mark.parametrize(
    "text,refusal",
    [
        ("response:\n  title:\n    en-us: x\n    pt-br: x\n", "response: unknown key"),
        ("errors:\n  not-found:\n    title: {en-us: x, pt-br: x}\n", "snake_case"),
        (
            "errors:\n  gone:\n    title: {en-us: x, pt-br: x}\n"
            "    message: {en-us: '{item} is gone', pt-br: x}\n",
            r"copy names a value as \$item",
        ),
        ("messages:\n  hint: {ref: nowhere}\n", "is no text of app/forms/vocabulary"),
    ],
    ids=["unknown block", "kebab key", "braced value", "unknown ref"],
)
def test_a_copy_its_schema_refuses_names_the_reason(
    copy_files: Files, text: str, refusal: str
) -> None:
    copy_files((COMMAND, "one", text))

    with pytest.raises(i18n.CopyError, match=refusal) as error:
        i18n.collect("en-us")
    assert COMMAND in str(error.value)


def test_a_ref_in_copy_is_the_text_of_the_shared_vocabulary(copy_files: Files) -> None:
    copy_files((COMMAND, "one", "messages:\n  hint: {ref: channel}\n"))

    assert i18n.collect("pt-br")["one.messages.hint"] == "Canal"
    assert i18n.collect("en-us")["one.messages.hint"] == "Channel"

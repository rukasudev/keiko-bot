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

ONE = SAMPLE.replace("sample.facts.", "one.facts.")

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
    text = "ui:\n  texts:\n    hint:\n      en-us: Hint\n      pt-br: Dica\n"
    copy_files((COMMAND, "one", text))

    assert i18n.collect("pt-br") == {"one.ui.texts.hint": "Dica"}
    assert i18n.collect("en-us") == {"one.ui.texts.hint": "Hint"}


def test_a_key_defined_in_two_files_names_both(copy_files: Files) -> None:
    copy_files(
        ("app/languages/texts.yml", "texts", OK),
        (COMMAND, "texts", OK),
    )

    with pytest.raises(i18n.CopyError) as error:
        i18n.collect("en-us")

    assert "app/languages/texts.yml" in str(error.value)
    assert COMMAND in str(error.value)


def test_a_key_in_one_file_and_a_block_in_another_names_both(
    copy_files: Files,
) -> None:
    block = "ok:\n  label:\n    en-us: Ok\n    pt-br: Ok\n"
    copy_files(
        ("app/languages/texts.yml", "texts", OK),
        (COMMAND, "texts", block),
    )

    with pytest.raises(i18n.CopyError) as error:
        i18n.collect("en-us")

    assert "texts.ok" in str(error.value)
    assert "app/languages/texts.yml" in str(error.value)
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
    copy_files((COMMAND, "one", ONE))

    flat = i18n.collect("pt-br")
    response = i18n.block("one.ui.responses.quiet", Response)

    assert flat["one.ui.responses.saved.description"] == "Salvei $item."
    assert "one.ui.responses.quiet.footer" not in flat, "a null text loads no key"
    assert response is not None and response.footer is None
    assert "footer" in response.model_fields_set


def test_a_typed_block_is_found_by_its_key_or_refused(copy_files: Files) -> None:
    copy_files((COMMAND, "one", ONE))

    assert i18n.block("one.ui.buttons.sync.label", Text) == Text.model_validate(
        {"en-us": "Sync", "pt-br": "Sincronizar"}
    )
    assert i18n.block("one.ui.responses.missing", Response, required=False) is None
    with pytest.raises(i18n.CopyError, match="no copy file writes it"):
        i18n.block("one.ui.responses.missing", Response)
    with pytest.raises(i18n.CopyError, match="not a Response"):
        i18n.block("one.ui.errors.not_found", Response)


@pytest.mark.parametrize(
    "text,refusal",
    [
        ("response:\n  title:\n    en-us: x\n    pt-br: x\n", "response: unknown key"),
        (
            "ui:\n  errors:\n    not-found:\n      title: {en-us: x, pt-br: x}\n",
            "snake_case",
        ),
        (
            "ui:\n  errors:\n    gone:\n      title: {en-us: x, pt-br: x}\n"
            "      message: {en-us: '{item} is gone', pt-br: x}\n",
            r"copy names a value as \$item",
        ),
        (
            "ui:\n  texts:\n    hint: {ref: nowhere}\n",
            "is no text of app/forms/vocabulary",
        ),
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


FACTS = (
    "facts:\n"
    "  seen: {type: boolean, description: Whether it was seen.}\n"
    "  shape: {type: enum, values: [round, square], description: The shape.}\n"
)
SAW = (
    "ui:\n  responses:\n    saw:\n      title: {en-us: x, pt-br: x}\n"
    "      overrides:\n        - when: %s\n          title: {en-us: y, pt-br: y}\n"
)


@pytest.mark.parametrize(
    "text,refusal",
    [
        (
            FACTS + SAW % "{key: one.facts.gone, is: true}",
            r"ui.responses.saw.overrides\[0\].when: one.facts.gone: "
            "no such fact in one",
        ),
        (
            FACTS + SAW % "{key: other.facts.seen, is: true}",
            "a copy rule reads a fact of one or global",
        ),
        (
            FACTS + SAW % "{key: one.facts.shape, is: oval}",
            r"'oval' is none of \['round', 'square'\]",
        ),
        (FACTS + SAW % "{key: one.facts.seen, is: maybe}", "'maybe' is no boolean"),
        (
            FACTS + SAW % "{key: one.facts.seen, matches: x}",
            "a boolean fact is tested with",
        ),
        (
            FACTS + "ui:\n  texts:\n    hint: {en-us: 'Hi $who', pt-br: 'Oi $who'}\n",
            r"ui.texts.hint: \$who is no fact of one or global",
        ),
        (
            "facts:\n  count: {type: number, description: A count.}\n",
            "facts.count: app/core/global.yml declares it too",
        ),
        (
            "ui:\n  texts:\n"
            "    hint: {en-us: 'Run {context:commands.nope}', pt-br: x}\n",
            "commands has no nope",
        ),
        (
            "ui:\n  texts:\n    hint: {en-us: 'On {context:server_name}', pt-br: x}\n",
            "is no context a copy reads",
        ),
    ],
    ids=[
        "unknown fact",
        "another command's fact",
        "enum value",
        "boolean value",
        "boolean operator",
        "undeclared dollar",
        "fact twice",
        "unknown command",
        "context filled at render",
    ],
)
def test_a_fact_or_a_context_the_catalogs_do_not_allow_is_refused_at_load(
    copy_files: Files, text: str, refusal: str
) -> None:
    copy_files((COMMAND, "one", text))

    with pytest.raises(i18n.CopyError, match=refusal) as error:
        i18n.collect("en-us")
    assert COMMAND in str(error.value)


def test_a_shared_file_reads_only_global_facts(copy_files: Files) -> None:
    shared = "app/languages/texts.yml"
    copy_files((shared, "texts", "hint: {en-us: 'Hi $who', pt-br: 'Oi $who'}\n"))

    with pytest.raises(i18n.CopyError, match=r"\$who is no fact of app/core/global"):
        i18n.collect("en-us")


def test_a_command_name_in_copy_is_its_slash_command_in_each_language(
    copy_files: Files,
) -> None:
    command, group = "{context:commands.block_links}", "{context:groups.moderations}"
    hint = (
        "ui:\n  texts:\n    hint:\n"
        f"      en-us: 'Run `{command}` or `{group}`'\n"
        f"      pt-br: 'Rode `{command}` ou `{group}`'\n"
    )
    copy_files((COMMAND, "one", hint))

    assert (
        i18n.collect("en-us")["one.ui.texts.hint"]
        == "Run `/moderations block links` or `/moderations`"
    )
    assert (
        i18n.collect("pt-br")["one.ui.texts.hint"]
        == "Rode `/moderações bloquear links` ou `/moderações`"
    )


def test_a_ref_in_copy_is_the_text_of_the_shared_vocabulary(copy_files: Files) -> None:
    copy_files((COMMAND, "one", "ui:\n  texts:\n    hint: {ref: channel}\n"))

    assert i18n.collect("pt-br")["one.ui.texts.hint"] == "Canal"
    assert i18n.collect("en-us")["one.ui.texts.hint"] == "Channel"

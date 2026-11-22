"""Keiko's own admin templates name only the variables the code fills.

A text an admin writes (a welcome message, a notification, a birthday message) reads
its variables in braces, `{user}`, filled by `fill_placeholders` (`app/core/utils.py`)
with the names each command's code passes. Keiko's YAML shows those names to the
admin (the variables a step explains, a modal's placeholder) and writes its own
defaults with them (a card's `defaults`, a copy's `ui.announcement.defaults`), and a
form's preview and fills name them again (`preview.tokens`, `fills.tokens`). Each
command fills one kind of template, so every `{name}` its copy and form write is
checked, as they load with each `${ref...}` written in, against the names its own
`fill_placeholders` calls pass, read from the syntax tree of its folder. A default is
the admin's own text once saved: Keiko fills nothing in it after it loads, so once the
loader has written in a command or a group of `${context...}` and a `${ref...}`, it
holds no token.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Set

import pytest

from app.core.commands import copy_files, form_files, strict_yaml
from app.core.facts import with_context, written_texts
from app.core.tokens import did_you_mean, plain, tokens
from app.forms.form.form_yaml import (
    Vocabulary,
    shared_vocabulary,
    vocabulary_from,
    written_out,
)
from tests.helpers.templates import filled_names

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
VOCABULARY = ROOT / "app" / "forms" / "vocabulary.yml"
BRACED = re.compile(r"\{([A-Za-z_][\w-]*)\}")


@dataclass(frozen=True)
class Problem:
    """A brace a command's YAML writes that its code never fills, or a token in a
    default."""

    path: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.where}: {self.message}"


def defaults(document: Any, where: str = "") -> Iterator[tuple[str, Any]]:
    """Each admin template default a copy or a form writes, with its path."""
    if isinstance(document, list):
        for index, item in enumerate(document):
            yield from defaults(item, f"{where}[{index}]")
    elif isinstance(document, dict):
        for key, value in document.items():
            path = f"{where}.{key}" if where else str(key)
            if key == "defaults" and isinstance(value, dict):
                yield path, value
            else:
                yield from defaults(value, path)


def declared_names(document: Any) -> Iterator[tuple[str, str]]:
    """Each variable a form's preview or fill declares, with its path."""
    for where, found in _blocks(document, ("preview", "fills"), ""):
        for name in (found.get("tokens") or {}) if isinstance(found, dict) else ():
            yield f"{where}.tokens.{name}", name


def _blocks(node: Any, names: tuple[str, ...], where: str) -> Iterator[tuple[str, Any]]:
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _blocks(item, names, f"{where}[{index}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            path = f"{where}.{key}" if where else str(key)
            if key in names:
                yield path, value
            yield from _blocks(value, names, path)


def problems(path: str, document: Any, filled: Set[str]) -> Iterator[Problem]:
    """Every brace of `document` its command never fills, and every token of a
    default that only a later pass would fill."""
    named = [
        (where, found)
        for where, words in written_texts(document)
        for found in BRACED.findall(plain(words))
    ]
    named += list(declared_names(document))
    for where, name in named:
        if name not in filled:
            close = did_you_mean(name, filled)
            hint = f"; did you mean {{{close}}}?" if close else ""
            yield Problem(
                path,
                where,
                f"{{{name}}} is no variable the code fills here "
                f"({', '.join(sorted(filled)) or 'none'}){hint}",
            )
    for where, block in defaults(document):
        for text_where, words in written_texts(block, where):
            for token in tokens(words):
                yield Problem(
                    path,
                    text_where,
                    f"{token.written}: a default is the admin's template, Keiko fills "
                    "nothing in it once it loads",
                )


def _copy_written_out(
    key: str, raw: Mapping[str, Any], vocabulary: Vocabulary
) -> Mapping[str, Any]:
    return with_context(raw, key, vocabulary.texts)


def _documents(
    vocabulary: Vocabulary | None = None,
    folder: str = "",
    typed: tuple[str, str] | None = None,
) -> Iterator[tuple[str, Path, Mapping[str, Any]]]:
    shared = vocabulary if vocabulary is not None else shared_vocabulary()
    for files, loaded in (
        (copy_files(), _copy_written_out),
        (form_files(), written_out),
    ):
        for key, path in sorted(files.items()):
            text = path.read_text(encoding="utf-8")
            if typed is not None and path.parent.name == folder:
                text = text.replace(*typed, 1)
            raw = strict_yaml(text, str(path)) or {}
            yield key, path, loaded(key, raw, shared)


def test_every_admin_variable_keiko_writes_is_one_its_command_fills():
    found = [
        str(problem)
        for _key, path, document in _documents()
        for problem in problems(
            str(path.relative_to(ROOT)), document, filled_names(path.parent)
        )
    ]

    assert found == [], "\n".join(found)


def test_the_check_reads_the_variables_each_command_fills():
    filled = {
        path.parent.name: filled_names(path.parent) for _k, path, _d in _documents()
    }

    assert filled["welcome"] == {"user", "server", "member_count"}
    assert filled["birthdays"] == {"user", "server", "date"}
    assert filled["twitch"] == {"streamer", "stream_link"}
    assert filled["youtube"] == {"youtuber", "video_link"}
    assert filled["block_links"] == {"user"}


@pytest.mark.parametrize(
    "folder,written,typo,message",
    [
        (
            "youtube",
            "{youtuber} just posted",
            "{youtubr} just posted",
            "{youtubr} is no variable the code fills here (video_link, youtuber); "
            "did you mean {youtuber}?",
        ),
        (
            "welcome",
            "Welcome {user} to",
            "Welcome {usr} to",
            "did you mean {user}?",
        ),
        (
            "youtube_live",
            "is live! {stream_link}",
            "is live! $count",
            "$count: a default is the admin's template",
        ),
        (
            "birthdays",
            "It's {user}'s birthday!",
            "It's {member}'s birthday!",
            "{member} is no variable the code fills here (date, server, user)",
        ),
    ],
    ids=["a youtuber typo", "a member typo", "a fact in a default", "a copy default"],
)
def test_a_wrong_variable_in_a_template_keiko_writes_is_caught(
    folder, written, typo, message
):
    """Probes of the syntax study: `{youtubr}` and `{usr}` in a default compiled and
    showed as typed to every member the template greeted."""
    found = [
        str(problem)
        for _key, path, document in _documents(folder=folder, typed=(written, typo))
        if path.parent.name == folder
        for problem in problems(path.name, document, filled_names(path.parent))
    ]

    assert any(message in problem for problem in found), found


@pytest.mark.parametrize("folder", ["twitch", "kick"])
def test_a_wrong_variable_in_the_shared_live_notice_is_caught_in_each_command(
    folder, tmp_path
):
    """Broke as: the check read each form as written and skipped its `${ref...}`, so
    a `{streamr}` in the live notice Twitch and Kick both default to passed, and every
    server that kept the default would have posted it as typed.

    Guaranteed: a wrong variable in a shared default fails in each command whose
    form names it."""
    source = VOCABULARY.read_text(encoding="utf-8")
    written = "{streamer} is live!"
    assert written in source
    typed = tmp_path / VOCABULARY.name
    typed.write_text(source.replace(written, "{streamr} is live!", 1), encoding="utf-8")

    found = [
        str(problem)
        for _key, path, document in _documents(vocabulary_from(typed))
        if path.parent.name == folder
        for problem in problems(path.name, document, filled_names(path.parent))
    ]

    assert any(
        "{streamr} is no variable the code fills here" in problem
        and "did you mean {streamer}?" in problem
        for problem in found
    ), found

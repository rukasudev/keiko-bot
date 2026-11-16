"""The index of the docs: one card per `docs/*.md`, its first heading and the start of
its first paragraph, and one for the YAML reference, in the groups of `GROUPS`."""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from scripts.keiko.yamldoc.page import TEMPLATES, slug, styled

from app.core.commands import YamlType

TEMPLATE = TEMPLATES / "index.html"
CARDS = "<!--docs-->"
REFERENCE = "yaml-reference.html"
REFERENCE_TITLE = "YAML reference"
REFERENCE_SUMMARY = (
    "Every key of every YAML file Keiko reads, generated from the schemas, with a "
    "real file of each type and every text in English and Portuguese."
)
SUMMARY_LENGTH = 180
SHORT_SUMMARY = 60
HEADING = re.compile(r"#\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"(```|~~~)")
NOT_PROSE = re.compile(r"(#|>|\||<|[-*+]\s|\d+[.)]\s|---|\*\*\*|___)")
CODE = re.compile(r"`[^`]*`")
HELD = re.compile("\x00(\\d+)\x00")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
ASIDE = re.compile(r"\s*\([^()]*\)")
STRONG = re.compile(r"(\*\*|__)(.+?)\1")
EMPHASIS = re.compile(r"(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*])")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`])")


@dataclass(frozen=True)
class Group:
    """A heading of the index: its name, what its docs are for and their files."""

    name: str
    says: str
    files: tuple[str, ...]


GROUPS: tuple[Group, ...] = (
    Group(
        "Guides",
        "How to make a change: what to read first, what to write and in which order.",
        (
            "agent-guide.md",
            "commands.md",
            "keiko-writing-style.md",
            "form-scenario-testing.md",
            "migrations.md",
            "releasing.md",
        ),
    ),
    Group(
        "References",
        "How a part of Keiko works today, kept current with the code.",
        (
            REFERENCE,
            "form-configuration.md",
            "data-model.md",
            "analytics.md",
            "testing-strategy.md",
        ),
    ),
    Group(
        "Histories",
        "Dated reviews and logs: what was found or changed, as of their day.",
        (
            "form-platform-architecture-review.md",
            "form-platform-error-history.md",
            "ux-changes.md",
        ),
    ),
)


@dataclass(frozen=True)
class Doc:
    """One doc as its card shows it: the file, its title and its summary."""

    file: str
    title: str
    summary: str


def read_doc(file: str, text: str) -> Doc:
    """The doc `file` holding the markdown `text`: its first heading and its first
    paragraph, shortened."""
    lines = text.splitlines()
    for number, line in enumerate(lines):
        heading = HEADING.fullmatch(line.strip())
        if heading:
            first = next(_paragraphs(lines[number + 1 :]), "")
            return Doc(file, _plain(heading.group(1)), summary(first))
    raise ValueError(f"docs/{file} has no `# ` heading to name its card by")


def summary(paragraph: str) -> str:
    """`paragraph` as one line: plain text with no aside in parentheses, its first
    sentence and the next ones while it is shorter than `SHORT_SUMMARY`, cut at a word
    when it is longer than `SUMMARY_LENGTH`."""
    held: list[str] = []
    text = CODE.sub(
        lambda found: _hold(held, found.group(0)), LINK.sub(r"\1", paragraph)
    )
    while ASIDE.search(text):
        text = ASIDE.sub("", text)
    text = HELD.sub(lambda found: held[int(found.group(1))], _plain(text))
    kept = ""
    for sentence in SENTENCE.split(text):
        if len(kept) >= SHORT_SUMMARY:
            break
        kept = f"{kept} {sentence}".strip()
    return kept if len(kept) <= SUMMARY_LENGTH else _cut(kept)


def unplaced(files: Sequence[str], groups: Sequence[Group] = GROUPS) -> list[str]:
    """What keeps `files` from one card each: a doc no group lists, a doc listed
    twice and a listed doc that does not exist."""
    listed = Counter(name for group in groups for name in group.files)
    known = {*files, REFERENCE}
    return [
        *(
            f"docs/{name} is in no group: add it to GROUPS in "
            "scripts/keiko/yamldoc/index.py"
            for name in sorted(set(files) - set(listed))
        ),
        *(
            f"docs/{name} is in {count} groups"
            for name, count in listed.items()
            if count > 1
        ),
        *(
            f"docs/{name} is listed but does not exist"
            for name in sorted(set(listed) - known)
        ),
    ]


def index(
    docs: Mapping[str, str],
    kinds: Sequence[YamlType],
    groups: Sequence[Group] = GROUPS,
) -> str:
    """The index page of `docs`, each markdown text by its file name, with the YAML
    reference and its `kinds`, in `groups`."""
    problems = unplaced(sorted(docs), groups)
    if problems:
        raise ValueError("; ".join(problems))
    read = {name: read_doc(name, text) for name, text in docs.items()}
    sections = "\n".join(_group(group, read, kinds) for group in groups)
    return styled(TEMPLATE).replace(CARDS, sections)


def _paragraphs(lines: Sequence[str]) -> Iterator[str]:
    block: list[str] = []
    fenced = False
    for line in [*lines, ""]:
        stripped = line.strip()
        if FENCE.match(stripped):
            fenced = not fenced
            block = []
        elif fenced:
            continue
        elif stripped:
            block.append(stripped)
        elif block:
            if not NOT_PROSE.match(block[0]):
                yield " ".join(block)
            block = []


def _hold(held: list[str], code: str) -> str:
    held.append(code)
    return f"\x00{len(held) - 1}\x00"


def _plain(text: str) -> str:
    text = LINK.sub(r"\1", text)
    text = STRONG.sub(r"\2", text)
    text = EMPHASIS.sub(r"\2", text)
    return " ".join(text.split())


def _cut(text: str) -> str:
    words: list[str] = []
    for word in CODE.sub(
        lambda found: found.group(0).replace(" ", "\x01"), text
    ).split():
        if len(" ".join([*words, word])) >= SUMMARY_LENGTH:
            break
        words.append(word)
    return " ".join(words).replace("\x01", " ").rstrip(",;:") + "…"


def _group(group: Group, read: Mapping[str, Doc], kinds: Sequence[YamlType]) -> str:
    cards = "\n".join(
        _reference(kinds) if name == REFERENCE else _card(read[name])
        for name in group.files
    )
    return (
        f'<section class="grp" id="{slug(group.name)}">\n'
        f"<h2>{_html(group.name)}</h2>\n"
        f'<p class="says">{_html(group.says)}</p>\n'
        f'<div class="cards">\n{cards}\n</div>\n'
        "</section>"
    )


def _card(doc: Doc, extra: str = "", kind: str = "card") -> str:
    return (
        f'<article class="{kind}">'
        f'<h3><a href="{html.escape(doc.file)}">{_html(doc.title)}</a></h3>'
        f"<code>docs/{html.escape(doc.file)}</code>"
        f'<p class="d">{_html(doc.summary)}</p>'
        f"{extra}</article>"
    )


def _reference(kinds: Sequence[YamlType]) -> str:
    links = "".join(
        f'<a href="{REFERENCE}#{slug(kind.name)}">{html.escape(kind.name)}</a>'
        for kind in kinds
    )
    doc = Doc(REFERENCE, REFERENCE_TITLE, REFERENCE_SUMMARY)
    return _card(doc, f'<p class="types">{links}</p>', "card wide")


def _html(text: str) -> str:
    return CODE.sub(
        lambda found: f"<code>{found.group(0)[1:-1]}</code>", html.escape(text)
    )

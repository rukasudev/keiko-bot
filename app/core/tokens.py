"""The one syntax a Keiko text writes a value with: `$name` or `${name}` for a fact,
`${answer.<key>}`, `${plural.<key>|<one>|<many>}`, `${context.<name>}` and
`${ref.<name>}` for the other kinds, and `$$` for a dollar; a pass reads a text once,
so a value it puts in is never read again."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Literal

Kind = Literal["fact", "answer", "plural", "context", "ref"]
KINDS: dict[str, Kind] = {
    "answer": "answer",
    "plural": "plural",
    "context": "context",
    "ref": "ref",
}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SHAPES: dict[Kind, re.Pattern[str]] = {
    "answer": re.compile(
        r"(?P<name>[A-Za-z0-9_]+)(?::(?P<format>[a-z_]+))?(?:\|(?P<fallback>.*))?",
        re.DOTALL,
    ),
    "plural": re.compile(
        r"(?P<name>[A-Za-z0-9_]+)\|(?P<one>[^|]*)\|(?P<many>.*)", re.DOTALL
    ),
    "context": re.compile(r"(?P<name>[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?)"),
    "ref": re.compile(r"(?P<name>[a-z][a-z0-9_]*)"),
}
SPELLINGS: dict[Kind, str] = {
    "answer": "${answer.<key>}, with :<format> and |<fallback> when needed",
    "plural": "${plural.<key>|<one>|<many>}",
    "context": "${context.<name>}",
    "ref": "${ref.<name>}",
}
OLD = re.compile(r"\{(context|answer|response|plural|ref):\s*([^}]*)\}")
OLD_KINDS = {"response": "answer"}


class TokenError(ValueError):
    """A text the syntax refuses: a `${` never closed, a kind it does not know, a
    token of the wrong shape, or a token in an old spelling."""


@dataclass(frozen=True)
class Token:
    """One value a text reads: its kind, the name it reads, how it is written, and
    the format, fallback or plural forms it carries."""

    kind: Kind
    name: str
    written: str
    format: str = ""
    fallback: str = ""
    one: str = ""
    many: str = ""


Piece = str | Token
Value = Callable[[Token], str | None]


def pieces(text: str) -> tuple[Piece, ...]:
    """The literal runs and the tokens of `text`, in order, a `$$` read as one `$`;
    refuses what the syntax does not allow."""
    return tuple(_scanned(text, strict=True))


def tokens(text: str) -> tuple[Token, ...]:
    """Every token `text` writes, refusing what the syntax does not allow."""
    return tuple(piece for piece in pieces(text) if isinstance(piece, Token))


def plain(text: str) -> str:
    """What `text` writes around its tokens, read as leniently as a pass reads it."""
    return "".join(
        piece for piece in _scanned(text, strict=False) if isinstance(piece, str)
    )


def fact_names(text: str) -> list[str]:
    """The name of every fact `text` reads, read as leniently as a pass reads it."""
    return [
        piece.name
        for piece in _scanned(text, strict=False)
        if isinstance(piece, Token) and piece.kind == "fact"
    ]


def resolved(text: str, value: Value, last: bool = True) -> str:
    """`text` with each token `value` gives a value for replaced by it, in one pass;
    a token with no value stays as written. The `last` pass writes each `$$` as `$`;
    an earlier one keeps it and doubles the `$` of each value it puts in, so the
    passes after it never read that value."""
    found = list(_scanned(text, strict=False))
    given = [value(piece) if isinstance(piece, Token) else None for piece in found]

    if not last and all(entry is None for entry in given):
        return text
    written = []
    for piece, entry in zip(found, given):
        if isinstance(piece, str):
            written.append(piece if last else _escaped(piece))
        elif entry is None:
            written.append(piece.written)
        else:
            written.append(entry if last else _escaped(entry))
    return "".join(written)


def included(text: str, shared: Callable[[str], str]) -> str:
    """`text` with each `${ref.<name>}` replaced by the shared text `shared` gives for
    the name, as that text is written, so its own tokens are read with the rest."""
    found = pieces(text)

    if not any(isinstance(piece, Token) and piece.kind == "ref" for piece in found):
        return text
    return "".join(
        shared(piece.name)
        if isinstance(piece, Token) and piece.kind == "ref"
        else _source(piece)
        for piece in found
    )


def finished(text: str) -> str:
    """`text` as it shows once no pass is left for it, each `$$` a `$`; a text that
    still reads a token is left as it is for the pass that fills it."""
    found = list(_scanned(text, strict=False))

    if any(isinstance(piece, Token) for piece in found):
        return text
    return "".join(piece for piece in found if isinstance(piece, str))


def did_you_mean(name: str, known: Iterable[str]) -> str | None:
    """The known name closest to `name`, None when none is close."""
    names = sorted(set(known))
    same = [entry for entry in names if entry.lower() == name.lower()]
    if same:
        return same[0]
    close = get_close_matches(name, names, n=1, cutoff=0.6)
    return close[0] if close else None


def hint(name: str, known: Iterable[str], spell: Callable[[str], str]) -> str:
    """`; did you mean <the closest known name, spelt>?`, empty when none is close."""
    close = did_you_mean(name, known)
    return f"; did you mean {spell(close)}?" if close else ""


def _scanned(text: str, strict: bool) -> Iterator[Piece]:
    literal: list[str] = []
    index = 0

    while index < len(text):
        piece, index = _piece_at(text, index, strict)
        if isinstance(piece, str):
            literal.append(piece)
            continue
        if literal:
            yield "".join(literal)
            literal = []
        yield piece
    if literal:
        yield "".join(literal)


def _piece_at(text: str, index: int, strict: bool) -> tuple[Piece, int]:
    char = text[index]
    if char == "{" and strict:
        _refuse_old(text, index)
    if char != "$":
        return char, index + 1
    following = text[index + 1 : index + 2]
    if following == "$":
        return "$", index + 2
    if following == "{":
        try:
            return _braced(text, index)
        except TokenError:
            if strict:
                raise
            return "$", index + 1
    name = NAME.match(text, index + 1)
    if name is None:
        return "$", index + 1
    return Token("fact", name.group(0), f"${name.group(0)}"), name.end()


def _braced(text: str, index: int) -> tuple[Token, int]:
    close = text.find("}", index + 2)
    if close < 0:
        raise TokenError(f"{text[index : index + 24]!r} is never closed by a }}")
    body = text[index + 2 : close]
    written = text[index : close + 1]

    if "${" in body:
        raise TokenError(f"{written} holds another token: a token's text is plain")
    if NAME.fullmatch(body):
        return Token("fact", body, written), close + 1
    head, dot, rest = body.partition(".")
    kind = KINDS.get(head)
    if not dot:
        raise TokenError(
            f"{written} is no token: a fact is ${{<name>}}, any other value "
            "${<kind>.<name>}"
        )
    if kind is None:
        closest = hint(head, KINDS, lambda known: f"${{{known}.{rest}}}")
        raise TokenError(
            f"{written}: {head} is no kind of token ({', '.join(KINDS)}){closest}"
        )
    shape = SHAPES[kind].fullmatch(rest)
    if shape is None:
        raise TokenError(f"{written} is written {SPELLINGS[kind]}")
    parts = shape.groupdict()
    token = Token(
        kind,
        parts["name"],
        written,
        format=parts.get("format") or "",
        fallback=parts.get("fallback") or "",
        one=parts.get("one") or "",
        many=parts.get("many") or "",
    )
    return token, close + 1


def _refuse_old(text: str, index: int) -> None:
    old = OLD.match(text, index)
    if old is None:
        return
    kind = OLD_KINDS.get(old.group(1), old.group(1))
    raise TokenError(
        f"{old.group(0)} is the old spelling: write ${{{kind}.{old.group(2).strip()}}}"
    )


def _escaped(words: str) -> str:
    return words.replace("$", "$$")


def _source(piece: Piece) -> str:
    return piece.written if isinstance(piece, Token) else _escaped(piece)

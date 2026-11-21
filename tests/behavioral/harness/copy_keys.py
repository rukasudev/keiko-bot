"""Which copy key wrote each text on screen, and with which values.

A golden that stores a sentence moves whenever the sentence is reworded. A
golden book stores the decision instead: while a scenario runs, `CopyTrace`
watches the seams every text crosses (a key read through python-i18n, a `Text`
read in a locale, and the three fills: a token pass, an admin's `{name}`
template, a `string.Template`), and `CopyTrace.keyer` then writes a rendered
string back as `⟨key⟩` or `⟨key: name=«value»⟩` wherever a traced text stands
in it. A fill is written with every value the code passed, the ones its locale
does not show too, so both locales key alike. What the code wrote around the
texts (markdown, emoji, separators, a number beside a copy word, the admin's
own values) stays as rendered.

A traced text is replaced only where it fills whole segments of the string
(between markdown separators, or a run of traced words), so a copy word inside
an admin's sentence stays as the admin wrote it. The whitespace around a copy
is layout, never part of it. The trace never changes what the app renders:
every patch returns what the original returns.
"""
import bisect
import re
import sys
from functools import lru_cache
from string import Template
from types import ModuleType
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

import i18n.translations as translations
from pydantic import BaseModel, RootModel

from app.core import tokens
from app.core.commands import Text
from app.forms.form.responses.summary import without_leading_emoji

OPEN, CLOSE = "⟨", "⟩"
VALUE_OPEN, VALUE_CLOSE = "«", "»"
NO_EMOJI = " (no emoji)"
LOWERCASE = " (lowercase)"
SHORTEST = 2
NUMBER = re.compile(r"#?\d+")
SEPARATORS = tuple(
    re.compile(pattern, re.MULTILINE)
    for pattern in (
        r"\s*(?:\*\*|`+|[()|·•/]|:(?=[\s*]|$)|, )\s*",
        r"\n+",
        r"^(?:#{1,3}|-#|-|>|\d+\.) ",
        r"(?:#\ufe0f?\u20e3|[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u23FF"
        r"\u25A0-\u27BF\u2B00-\u2BFF\ufe0f\u200d])+ +",
        r":[a-z0-9_+-]+: +",
    )
)

Table = List[Tuple[str, str]]


def _segment(node: Any, index: int) -> str:
    for name in ("key", "value", "name"):
        value = node.get(name) if isinstance(node, dict) else getattr(node, name, None)
        if isinstance(value, str) and value:
            return value
    return str(index)


def _walk(node: Any, path: str, seen: set) -> Iterator[Tuple[str, Text]]:
    if isinstance(node, Text):
        yield path, node
        return
    if id(node) in seen:
        return
    seen.add(id(node))
    if isinstance(node, RootModel):
        yield from _walk(node.root, path, seen)
    elif isinstance(node, BaseModel):
        for name in type(node).model_fields:
            yield from _walk(getattr(node, name), f"{path}.{name}", seen)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}", seen)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}.{_segment(value, index)}", seen)


def _sources() -> List[Tuple[str, Any]]:
    from app.core.i18n import typed
    from app.forms.form.form_yaml import DiskSource, registry, shared_vocabulary

    sources: List[Tuple[str, Any]] = list(typed().items())
    sources.append(("vocabulary", shared_vocabulary()))
    sources += [(f"{key}.form", registry.get(key)) for key in DiskSource().list()]
    return sources


@lru_cache(maxsize=1)
def text_keys() -> Dict[Tuple[str, str], str]:
    """Every `Text` the app loads, by its two locales, named by where it is written:
    the copy files first, then the shared vocabulary, then each form."""
    named: Dict[Tuple[str, str], str] = {}
    for prefix, root in _sources():
        for path, text in _walk(root, prefix, set()):
            named.setdefault((text.en_us, text.pt_br), path)
    return named


@lru_cache(maxsize=1)
def texts_by_key() -> Dict[str, Text]:
    """Every `Text` the app loads, by the key a golden book writes it under."""
    named: Dict[str, Text] = {}
    for prefix, root in _sources():
        for path, text in _walk(root, prefix, set()):
            named.setdefault(path, text)
    return named


def copy_text(key: str, locale: str, **values: Any) -> str:
    """What the copy key `key` writes in `locale`, named as a golden book names it
    (a form's `<form>.form.steps...`, the `vocabulary...` or a copy file's key), each
    `$name` of `values` filled the way the app fills it."""
    from app.core.utils import ml

    text = texts_by_key().get(key)
    written = text.get(locale) if text is not None else ml(key, locale)
    if not values:
        return written
    return tokens.resolved(
        written, lambda token: str(values[token.name]) if token.name in values else None
    )


def _patch_everywhere(monkeypatch, original: Callable, replacement: Callable) -> None:
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", None) or ""
        if not isinstance(module, ModuleType) or not name.startswith("app"):
            continue
        for attribute, value in list(vars(module).items()):
            if value is original:
                monkeypatch.setattr(module, attribute, replacement)


def _fact_name(token: Any) -> str:
    return f"{token.name}:{token.format}" if token.format else token.name


def _value_of(token: Any, given: str) -> str:
    if token.kind == "plural":
        return "many" if given == token.many and given != token.one else "one"
    return given


def _longest_first(pairs: Table) -> Table:
    return sorted(pairs, key=lambda pair: (-len(pair[0]), pair[0]))


def _bounds(rendered: str) -> List[int]:
    found = {0, len(rendered)}
    for separator in SEPARATORS:
        for match in separator.finditer(rendered):
            found.update((match.start(), match.end()))
    return sorted(found)


def _whole(rendered: str, index: int, table: Table, bounds: List[int]):
    for text, keyed in table:
        end = index + len(text)
        if rendered.startswith(text, index) and _is_bound(end, bounds):
            return end, keyed
    return None


def _is_bound(index: int, bounds: List[int]) -> bool:
    position = bisect.bisect_left(bounds, index)
    return position < len(bounds) and bounds[position] == index


def _word_end(segment: str, end: int) -> bool:
    return end == len(segment) or segment[end] == " "


def _number_after_copy(segment: str, index: int, out: List[str]) -> Optional[int]:
    found = NUMBER.match(segment, index)
    if found is None or not any(piece.startswith(OPEN) for piece in out):
        return None
    return found.end() if _word_end(segment, found.end()) else None


def _covered(segment: str, table: Table) -> Optional[str]:
    out: List[str] = []
    index = 0
    while index < len(segment):
        if segment[index] == " ":
            out.append(" ")
            index += 1
            continue
        for text, keyed in table:
            end = index + len(text)
            if segment.startswith(text, index) and _word_end(segment, end):
                out.append(keyed)
                index = end
                break
        else:
            end = _number_after_copy(segment, index, out)
            if end is None:
                return None
            out.append(segment[index:end])
            index = end
    return "".join(out)


def replaced(rendered: str, table: Table) -> str:
    """`rendered` with each text of `table` that fills whole segments replaced."""
    bounds = _bounds(rendered)
    out: List[str] = []
    index = 0
    while index < len(rendered):
        if _is_bound(index, bounds):
            hit = _whole(rendered, index, table, bounds)
            if hit is not None:
                index, keyed = hit
                out.append(keyed)
                continue
            end = bounds[bisect.bisect_right(bounds, index)]
            covered = _covered(rendered[index:end], table)
            if covered is not None and end > index:
                out.append(covered)
                index = end
                continue
        out.append(rendered[index])
        index += 1
    return "".join(out)


def _passed(value: str, texts: Table, lowered: Table) -> str:
    keyed = replaced(value, texts)
    return keyed if keyed != value else replaced(value, lowered)


class CopyTrace:
    """The copy one scenario read, and the values each fill passed."""

    def __init__(self) -> None:
        self.read: Dict[str, set] = {}
        self.fills: List[Tuple[str, str, Dict[str, str]]] = []
        self._table: Optional[Table] = None

    def install(self, monkeypatch) -> "CopyTrace":
        """Watch the copy seams until the test ends."""
        original_get = translations.get
        original_text = Text.get
        original_resolved = tokens.resolved
        trace = self

        def get(key, locale=None):
            value = original_get(key, locale=locale)
            if isinstance(value, str):
                trace.read.setdefault(value, set()).add(key)
            return value

        def text_get(self_text, locale):
            value = original_text(self_text, locale)
            key = text_keys().get((self_text.en_us, self_text.pt_br))
            if key is not None:
                trace.read.setdefault(value, set()).add(key)
            return value

        def resolved(text, value, last=True):
            answered: Dict[str, str] = {}

            def spy(token):
                given = value(token)
                if given is not None:
                    answered[_fact_name(token)] = _value_of(token, given)
                return given

            output = original_resolved(text, spy, last)
            if last and answered:
                trace.filled(text, output, answered)
            return output

        monkeypatch.setattr(translations, "get", get)
        monkeypatch.setattr(Text, "get", text_get)
        _patch_everywhere(monkeypatch, original_resolved, resolved)
        self._watch_templates(monkeypatch)
        return self

    def _watch_templates(self, monkeypatch) -> None:
        original_templated = tokens.templated
        original_substitute = Template.safe_substitute
        trace = self

        def templated(template, values):
            output = original_templated(template, values)
            trace.filled(template, output, values)
            return output

        def safe_substitute(self_template, mapping=None, /, **values):
            given = {**(mapping or {}), **values}
            output = original_substitute(self_template, given)
            trace.filled(self_template.template, output, given)
            return output

        monkeypatch.setattr(Template, "safe_substitute", safe_substitute)
        _patch_everywhere(monkeypatch, original_templated, templated)

    def filled(self, template: str, output: str, values: Dict[str, Any]) -> None:
        """Note a fill that changed `template`, with every value the code passed."""
        if output != template and values:
            given = {name: str(value) for name, value in values.items()}
            self.fills.append((template, output, given))

    def _texts(self, lowercase: bool = False) -> Table:
        found: Table = []
        lowered: Table = []
        for template, keys in self.read.items():
            key = min(keys)
            text = template.strip()
            if len(text) < SHORTEST or tokens.fact_names(template):
                continue
            found.append((text, f"{OPEN}{key}{CLOSE}"))
            bare = without_leading_emoji(text)
            if bare != text:
                found.append((bare, f"{OPEN}{key}{NO_EMOJI}{CLOSE}"))
            if lowercase and text.lower() != text:
                lowered.append((text.lower(), f"{OPEN}{key}{LOWERCASE}{CLOSE}"))
        exact = {text for text, _keyed in found}
        lowered = [pair for pair in lowered if pair[0] not in exact]
        return _longest_first(found + lowered)

    def table(self) -> Table:
        """Each traced text as rendered, longest first, with what stands for it."""
        if self._table is None:
            self._table = self._built()
        return self._table

    def _built(self) -> Table:
        texts = self._texts()
        lowered = self._texts(lowercase=True)
        found: Dict[str, str] = {}
        for template, output, values in self.fills:
            if template not in self.read or len(output.strip()) < SHORTEST:
                continue
            key = min(self.read[template])
            facts = "; ".join(
                f"{name}={VALUE_OPEN}{_passed(value, texts, lowered)}{VALUE_CLOSE}"
                for name, value in sorted(values.items())
            )
            found.setdefault(output.strip(), f"{OPEN}{key}: {facts}{CLOSE}")
        for template, keyed in texts:
            found.setdefault(template, keyed)
        return _longest_first(list(found.items()))

    def keyer(self, admin: Iterable[str] = ()) -> Callable[[str], str]:
        """A function writing a rendered string with every traced text as its key,
        never a text that is also one of the `admin`'s own values."""
        kept = set(admin)
        table = [(text, keyed) for text, keyed in self.table() if text not in kept]
        return lambda rendered: replaced(rendered, table) if rendered else rendered

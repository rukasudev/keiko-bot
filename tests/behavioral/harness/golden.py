"""Golden books: what each canonical path shows, pinned to one file per form.

A golden is the definition of "the admin sees the same thing" (review II.3):
a later run of the same path must reproduce it exactly. A change to what an
admin sees is listed in `docs/ux-changes.md` first, and the books are
re-recorded once, at the end of the change
(`pytest tests/behavioral/golden --update-golden`).

Component ids never enter a book. They are the one normalized field the user
cannot see (`action` on a component, `custom_id` on the click that hit it:
both a custom_id production code chose), and the engine encodes session and
revision in every id. The harness's own `step` annotation stays out for the
same reason: it names the engine's cursor, not anything on screen.

Transport is not compared either: a screen edited through the initial
response, a followup or the original-response endpoint looks the same, and a
`defer` without "thinking" shows nothing. Every run is canonicalized to
`send`, `edit`, `delete` and the visible `defer` before it is recorded or
compared, so an engine is free to choose the call, never the outcome.

Each form keeps `tests/behavioral/golden/<form>.yml`. Its `screens` hold every
distinct screen once, under a name the recorder derives from the copy the
screen opens with; a screen close to another of its family is stored as
`from: <base>` and the `changes` the recorder computed, never written by hand.
Its `flows` hold each path as its steps: the user's action, then one line per
answer of the bot naming the screen it showed. Every text is written as the
copy key that wrote it and the values the code passed (`copy_keys.py`), so a
reworded copy moves no book; the screens a form names in `rendered` (its panel
and its review) also keep their words in every locale a flow shows them in, to
catch a fill that broke or a bad translation. On replay every screen is
compared whole, base plus changes.
"""
import copy
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml

from tests.behavioral.harness.errors import ScenarioAssertionError

Events = List[Dict[str, Any]]

INVISIBLE_FIELDS = ("action", "custom_id", "step")
TRANSPORT_KINDS = {
    "followup_edit": "edit",
    "edit_original": "edit",
    "followup_send": "send",
    "delete_original": "delete",
}
DIFF_LINES = 80


def _strip_invisible(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _strip_invisible(value)
            for key, value in node.items()
            if key not in INVISIBLE_FIELDS
        }
    if isinstance(node, list):
        return [_strip_invisible(item) for item in node]
    return node


CONTENT_KINDS = ("send", "edit", "followup_send", "followup_edit", "edit_original")


def _with_component_defaults(event: Dict[str, Any]) -> Dict[str, Any]:
    """A message sent or edited without a view shows no components: say so."""
    if event.get("actor") == "bot" and event.get("kind") in CONTENT_KINDS:
        event.setdefault("components", [])
        event.setdefault("components_v2", False)
    for field in (event.get("modal") or {}).get("fields", []):
        if "default" in field and field["default"] is None:
            field["default"] = ""
    return event


def project(events: Events) -> Events:
    """The events as a book stores them: a deep copy without invisible fields."""
    return [
        _with_component_defaults(_strip_invisible(copy.deepcopy(event)))
        for event in events
    ]


def _is_silent_defer(event: Dict[str, Any]) -> bool:
    return event.get("kind") == "defer" and not event.get("thinking")


def canonical(events: Events) -> Events:
    """The events with transport folded away: what the admin could tell apart."""
    kept = []
    for event in events:
        if _is_silent_defer(event):
            continue
        folded = dict(event)
        folded["kind"] = TRANSPORT_KINDS.get(event.get("kind"), event.get("kind"))
        kept.append(folded)
    return _renumber(kept)


def dump(events: Any) -> str:
    return json.dumps(events, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _renumber(events: Events) -> Events:
    for index, event in enumerate(events, start=1):
        event["seq"] = index
    return events


BOOK_ENVELOPE = ("seq", "actor", "kind", "message", "delete_after", "thinking")
BOOK_EXTRAS = ("delete_after", "thinking")
UNKEYED_FIELDS = ("type", "style", "media", "thumbnail", "image", "value", "color")
STORED_ONLY = ("from", "changes", "rendered")
SAVED_LABELS = ("title", "style")
GENERIC_PARTS = {
    "title", "label", "description", "message", "content", "text", "name",
    "placeholder", "overrides", "steps", "root", "modal", "inputs",
}
KEY_IN_TEXT = re.compile(r"⟨([^⟩:(]+?)(?: \([a-z ]+\))?(?::|⟩)")
LINE = re.compile(
    r"^(?P<kind>\S+)(?: (?P<message>M\d+))?(?: (?P<screen>[^\s(]+))?(?: \(.*\))?$"
)
NAME_LENGTH = 40
VARIANT_SHARE = 0.5

Record = Tuple[Dict[str, Any], Dict[str, Any]]


def _keyed_node(node: Any, keyer: Callable[[str], str], field: str = "") -> Any:
    if isinstance(node, dict):
        return {
            key: value if key in BOOK_ENVELOPE else _keyed_node(value, keyer, key)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_keyed_node(item, keyer, field) for item in node]
    if isinstance(node, str) and field not in UNKEYED_FIELDS:
        return keyer(node)
    return node


def _strings(node: Any, field: str = "") -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in BOOK_ENVELOPE:
                yield from _strings(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item, field)
    elif isinstance(node, str) and field not in UNKEYED_FIELDS:
        yield node


def _leaves(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in SAVED_LABELS:
                yield from _leaves(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _leaves(item)
    elif isinstance(node, (str, int)) and not isinstance(node, bool):
        yield str(node)


def _typed(event: Dict[str, Any]) -> List[str]:
    if event.get("actor") != "user" or event.get("kind") == "start_command":
        return []
    return [*_leaves(event.get("values")), *_leaves(event.get("fields"))]


def keyed_records(scenario: Any, trace: Any) -> List[Record]:
    """Each canonical event with its texts written as copy keys, beside the event
    as rendered; what the admin had saved, and what they typed or picked before
    an answer, is never read back as copy in it."""
    admin = set(_leaves(getattr(scenario, "saved_at_open", None) or []))
    keyer = trace.keyer(admin)
    records: List[Record] = []
    for event in canonical(project(scenario.outputs)):
        typed = set(_typed(event)) - admin
        if typed:
            admin |= typed
            keyer = trace.keyer(admin)
        keyed = _keyed_node(event, keyer) if event["actor"] == "bot" else event
        records.append((keyed, event))
    return records


def _screen(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if event.get("actor") != "bot":
        return None
    shown = {key: value for key, value in event.items() if key not in BOOK_ENVELOPE}
    return shown or None


def _rendered_texts(keyed: Dict[str, Any], rendered: Dict[str, Any]) -> List[str]:
    return [
        text
        for written, text in zip(_strings(keyed), _strings(rendered))
        if written != text
    ]


def _inline(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def user_line(event: Dict[str, Any]) -> str:
    parts = [event["kind"]]
    if event.get("target") is not None:
        parts.append(str(event["target"]))
    for field in ("values", "fields"):
        if event.get(field) is not None:
            parts.append(_inline(event[field]))
    return " ".join(parts)


def bot_line(event: Dict[str, Any], screen: Optional[str]) -> str:
    parts = [event["kind"]]
    if event.get("message"):
        parts.append(event["message"])
    if screen:
        parts.append(screen)
    extras = [
        f"{field} {_inline(event[field])}"
        for field in BOOK_EXTRAS
        if event.get(field) not in (None, False)
    ]
    if extras:
        parts.append(f"({', '.join(extras)})")
    return " ".join(parts)


def _meaning(key: str) -> str:
    parts = key.split(".")
    while len(parts) > 1 and (parts[-1] in GENERIC_PARTS or parts[-1].isdigit()):
        parts.pop()
    word = parts[-1]
    for suffix in ("_description", "_title"):
        word = word.removesuffix(suffix) or word
    return word


def _shape(screen: Dict[str, Any]) -> str:
    if "modal" in screen:
        return "modal"
    if screen.get("components_v2"):
        return "card"
    return "embed" if screen.get("embed") else "message"


def family_of(screen: Dict[str, Any]) -> Tuple[str, str]:
    """A screen's name from the copy it opens with (its title's key, and the key of
    the text under it when that one names something else), and its shape."""
    keyed = [
        found for found in (KEY_IN_TEXT.findall(text) for text in _strings(screen))
        if found
    ]
    if not keyed:
        return "screen", _shape(screen)
    first = _meaning(keyed[0][0])
    second = _meaning(keyed[1][0]) if len(keyed) > 1 else first
    named = second in (first, "footer")
    return (first if named else f"{first}.{second}"), _shape(screen)


def _action_label(event: Dict[str, Any]) -> str:
    if event["kind"] == "select":
        picked = ",".join(_leaves(event.get("values")))
        target = str(event.get("target") or "")
        label = picked if " " in target or not target else f"{target}={picked}"
    elif event["kind"] == "click":
        label = str(event.get("target"))
    else:
        label = event["kind"]
    return re.sub(r"[^\w:=,$.-]+", "_", label).strip("_")[:NAME_LENGTH]


def _path(path: str, part: Any) -> str:
    return f"{path}.{part}" if path else str(part)


def changes(base: Any, target: Any, path: str = "") -> List[Dict[str, Any]]:
    """The edits that turn `base` into `target`, in the order they apply."""
    if base == target:
        return []
    if isinstance(base, dict) and isinstance(target, dict):
        found = [{f"drop {_path(path, key)}": None} for key in base if key not in target]
        for key, value in target.items():
            if key in base:
                found += changes(base[key], value, _path(path, key))
            else:
                found.append({f"set {_path(path, key)}": value})
        return found
    if isinstance(base, list) and isinstance(target, list):
        return _list_changes(base, target, path)
    return [{f"set {path}": target}]


def _list_changes(base: List[Any], target: List[Any], path: str) -> List[Dict[str, Any]]:
    matcher = difflib.SequenceMatcher(
        a=[dump(item) for item in base], b=[dump(item) for item in target], autojunk=False
    )
    found: List[Dict[str, Any]] = []
    offset = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        paired = min(i2 - i1, j2 - j1)
        for step in range(paired):
            at = _path(path, i1 + step + offset)
            found += changes(base[i1 + step], target[j1 + step], at)
        at = i1 + paired + offset
        found += [{f"drop {_path(path, at)}": None} for _ in range(i2 - i1 - paired)]
        found += [
            {f"add {_path(path, at + step)}": target[j1 + paired + step]}
            for step in range(j2 - j1 - paired)
        ]
        offset += (j2 - j1) - (i2 - i1)
    return found


def _parent(screen: Any, path: str) -> Tuple[Any, Any]:
    parts = [int(part) if part.isdigit() else part for part in path.split(".")]
    node = screen
    for part in parts[:-1]:
        node = node[part]
    return node, parts[-1]


def apply_changes(base: Dict[str, Any], edits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`base` with `edits` applied in order."""
    screen = copy.deepcopy(base)
    for edit in edits:
        ((verb_path, value),) = edit.items()
        verb, _, path = verb_path.partition(" ")
        parent, last = _parent(screen, path)
        if verb == "set":
            parent[last] = copy.deepcopy(value)
        elif verb == "add":
            parent.insert(last, copy.deepcopy(value))
        else:
            del parent[last]
    return screen


def expand(book: Dict[str, Any], name: str) -> Dict[str, Any]:
    """The whole screen `name` stands for: its base with its changes applied."""
    stored = book["screens"][name]
    if "from" in stored:
        return apply_changes(expand(book, stored["from"]), stored.get("changes") or [])
    return {key: value for key, value in stored.items() if key not in STORED_ONLY}


def build_book(flows: Dict[str, Tuple[str, List[Record]]],
               rendered: Iterable[str] = ()) -> Dict[str, Any]:
    """The book of a command's flows: each distinct screen once, a variant as its
    changes from the nearest base of its family, and each flow as its lines."""
    seen: Dict[str, Dict[str, Any]] = {}
    for flow_id in sorted(flows):
        locale, records = flows[flow_id]
        action = "open"
        for keyed, shown in records:
            if keyed["actor"] != "bot":
                action = _action_label(keyed)
                continue
            screen = _screen(keyed)
            if screen is None:
                continue
            info = seen.setdefault(dump(screen), {
                "screen": screen, "refs": 0, "order": len(seen), "action": action,
                "flow": flow_id.rsplit(".", 1)[0], "rendered": {},
            })
            info["refs"] += 1
            info["rendered"].setdefault(locale, _rendered_texts(keyed, shown))

    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for info in sorted(seen.values(), key=lambda entry: entry["order"]):
        grouped.setdefault(family_of(info["screen"]), []).append(info)
    families: Dict[str, List[Dict[str, Any]]] = {}
    for (name, shape), members in grouped.items():
        families[name if name not in families else f"{name}.{shape}"] = members

    screens: Dict[str, Any] = {}
    names: Dict[str, str] = {}
    keep_rendered = set(rendered)
    for family, members in families.items():
        bases: List[Tuple[str, Dict[str, Any]]] = []
        for info in sorted(members, key=lambda entry: (-entry["refs"], entry["order"])):
            name = family if not bases else _free_name(family, info, screens)
            screen = info["screen"]
            nearest = min(
                ((base, changes(shape, screen)) for base, shape in bases),
                key=lambda pair: len(dump(pair[1])),
                default=None,
            )
            small = VARIANT_SHARE * len(dump(screen))
            if nearest is not None and len(dump(nearest[1])) <= small:
                screens[name] = {"from": nearest[0], "changes": nearest[1]}
            else:
                screens[name] = dict(screen)
                bases.append((name, screen))
            names[dump(screen)] = name
        if family in keep_rendered:
            _keep_words(screens, members, names)

    book_flows: Dict[str, List[Any]] = {}
    for flow_id in sorted(flows):
        steps: List[Dict[str, List[str]]] = []
        for keyed, _shown in flows[flow_id][1]:
            if keyed["actor"] != "bot":
                steps.append({user_line(keyed): []})
                continue
            if not steps:
                steps.append({"open": []})
            screen = _screen(keyed)
            line = bot_line(keyed, names[dump(screen)] if screen else None)
            next(iter(steps[-1].values())).append(line)
        book_flows[flow_id] = [
            next(iter(step)) if not next(iter(step.values())) else step for step in steps
        ]
    book = {"screens": screens, "flows": book_flows}
    kept = {name for name, stored in screens.items() if "rendered" in stored}
    if keep_rendered - kept:
        raise ValueError(
            f"no screen {sorted(keep_rendered - kept)} to keep the words of; "
            f"the book names {sorted(families)}"
        )
    for info in seen.values():
        assert expand(book, names[dump(info["screen"])]) == info["screen"]
    return book


def _keep_words(screens: Dict[str, Any], members: List[Dict[str, Any]],
                names: Dict[str, str]) -> None:
    """The words of a rendered family once in every locale it shows: on its base,
    or, for a locale that never shows the base, on the family's screen it shows most."""
    kept: Dict[str, Dict[str, List[str]]] = {}
    shown: set = set()
    for info in sorted(members, key=lambda entry: (-entry["refs"], entry["order"])):
        name = names[dump(info["screen"])]
        for locale, words in sorted(info["rendered"].items()):
            if locale not in shown:
                kept.setdefault(name, {})[locale] = words
                shown.add(locale)
    for name, words in kept.items():
        screens[name]["rendered"] = dict(sorted(words.items()))


def _free_name(family: str, info: Dict[str, Any], taken: Dict[str, Any]) -> str:
    name = f"{family}+{info['action']}"
    if name in taken:
        name = f"{name}@{info['flow']}"
    stem, count = name, 2
    while name in taken:
        name, count = f"{stem}#{count}", count + 1
    return name


def flow_lines(steps: List[Any]) -> List[str]:
    """A flow's steps as one line per event, the user's and the bot's in order."""
    lines: List[str] = []
    for step in steps:
        if isinstance(step, str):
            lines.append(step)
            continue
        ((action, answers),) = step.items()
        if action != "open":
            lines.append(action)
        lines.extend(answers or [])
    return lines


class _BookDumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, data: str) -> Any:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def _represent_dict(dumper: yaml.SafeDumper, data: Dict[Any, Any]) -> Any:
    flat = len(data) > 1 and all(
        not isinstance(value, (dict, list)) and "\n" not in str(value)
        and " " not in str(key)
        for key, value in data.items()
    )
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=flat)


_BookDumper.add_representer(str, _represent_str)
_BookDumper.add_representer(dict, _represent_dict)


def dump_book(book: Dict[str, Any]) -> str:
    return yaml.dump(
        book, Dumper=_BookDumper, allow_unicode=True, sort_keys=False,
        width=4096, default_flow_style=False,
    )


@lru_cache(maxsize=8)
def _parsed_book(path: str, version: Tuple[int, int]) -> Dict[str, Any]:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return yaml.load(Path(path).read_text(encoding="utf-8"), Loader=loader)


def load_book(path: Path) -> Dict[str, Any]:
    """The book at `path`, parsed once per version of the file."""
    stat = path.stat()
    return _parsed_book(str(path), (stat.st_mtime_ns, stat.st_size))


def _screen_problem(name: Optional[str], expected: Optional[Dict[str, Any]],
                    actual: Dict[str, Any]) -> str:
    title = f"screen {name!r} differs" if expected is not None else f"no screen {name!r}"
    diff = difflib.unified_diff(
        dump(expected or {}).splitlines(), dump(actual).splitlines(),
        fromfile=f"{name} (base and changes)", tofile="actual", lineterm="", n=2,
    )
    return "\n".join([title, *list(diff)[:DIFF_LINES]])


def book_mismatch(book: Dict[str, Any], flow_id: str, locale: str,
                  records: List[Record]) -> Optional[str]:
    """Why the run does not reproduce the flow, or None when it does: every screen
    compared whole against its base and changes, every line in order."""
    if flow_id not in book["flows"]:
        return f"no flow {flow_id!r}; record it with `pytest --update-golden`"
    expected = flow_lines(book["flows"][flow_id])
    actual: List[str] = []
    problems: List[str] = []
    for index, (keyed, shown) in enumerate(records):
        if keyed["actor"] != "bot":
            actual.append(user_line(keyed))
            continue
        screen = _screen(keyed)
        name = None
        if screen is not None:
            match = LINE.match(expected[index]) if index < len(expected) else None
            name = match.group("screen") if match else None
            stored = book["screens"].get(name) if name else None
            want = expand(book, name) if stored is not None else None
            if want != screen:
                problems.append(_screen_problem(name, want, screen))
                name = f"{name}?"
            elif locale in (stored.get("rendered") or {}):
                texts = _rendered_texts(keyed, shown)
                if texts != stored["rendered"][locale]:
                    words = stored["rendered"][locale]
                    problems.append(_rendered_problem(name, words, texts))
        actual.append(bot_line(keyed, name))
    if actual == expected and not problems:
        return None
    lines = list(difflib.unified_diff(
        expected, actual, fromfile=f"{flow_id} (book)", tofile="actual", lineterm="", n=3,
    ))
    return "\n\n".join(filter(None, ["\n".join(lines), *problems[:2]]))


def _rendered_problem(name: str, expected: List[str], actual: List[str]) -> str:
    diff = difflib.unified_diff(
        expected, actual, fromfile=f"{name} rendered", tofile="actual", lineterm="", n=1,
    )
    return "\n".join([f"screen {name!r} renders other words", *list(diff)[:DIFF_LINES]])


def assert_book(scenario: Any, trace: Any, path: Path, flow_id: str, locale: str) -> None:
    """Fail with the differing lines and screens unless the run reproduces the flow."""
    if not path.exists():
        raise ScenarioAssertionError(
            f"no golden book at {path}; record it with `pytest --update-golden`",
            scenario.transcript,
        )
    records = keyed_records(scenario, trace)
    problem = book_mismatch(load_book(path), flow_id, locale, records)
    if problem is not None:
        raise ScenarioAssertionError(f"flow differs from book {path}\n{problem}",
                                     scenario.transcript)


class BookRecorder:
    """The flows an `--update-golden` run recorded, by form, until they are written."""

    def __init__(self) -> None:
        self.flows: Dict[str, Dict[str, Tuple[str, List[Record]]]] = {}
        self.expected: Dict[str, frozenset] = {}
        self.rendered: Dict[str, Tuple[str, ...]] = {}

    def add(self, form: str, flow_id: str, locale: str, records: List[Record],
            expected: Iterable[str], rendered: Iterable[str]) -> None:
        self.flows.setdefault(form, {})[flow_id] = (locale, records)
        self.expected[form] = frozenset(expected)
        self.rendered[form] = tuple(rendered)

    def write(self, root: Path) -> List[str]:
        """Write the book of every form whose flows all ran; the others' names."""
        skipped = []
        for form, flows in self.flows.items():
            if set(flows) != self.expected[form]:
                skipped.append(form)
                continue
            book = build_book(flows, self.rendered[form])
            (root / f"{form}.yml").write_text(dump_book(book), encoding="utf-8")
        return skipped

"""Golden transcripts: a scenario's normalized output, pinned to a JSON file.

A golden is the definition of "the admin sees the same thing" (review II.3):
a later run of the same path must reproduce it exactly. A deliberate change
is listed in `docs/ux-changes.md` first, gets a canonicalizer in `DELTAS`
under its id, is passed by id in `allowed` while the goldens are re-recorded,
and the canonicalizer is dropped once they are. `docs/ux-changes.md` keeps
the nine deltas the platform migration introduced (ux-1 to ux-9).

Component ids never enter a golden. They are the one normalized field the
user cannot see (`action` on a component, `custom_id` on the click that hit
it: both a custom_id production code chose), and the new engine encodes
session and revision in every id. The harness's own `step` annotation stays
out for the same reason: it names the engine's cursor, not anything on screen.

Transport is not compared either: a screen edited through the initial
response, a followup or the original-response endpoint looks the same, and a
`defer` without "thinking" shows nothing. Both sides are canonicalized to
`send`, `edit`, `delete` and the visible `defer` before they are compared, so
an engine is free to choose the call, never the outcome.
"""
import copy
import difflib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from tests.behavioral.harness.errors import ScenarioAssertionError
from tests.behavioral.harness.transcript import format_event

Events = List[Dict[str, Any]]
Delta = Callable[[Events, Events], Tuple[Events, Events]]

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
    """The events as a golden stores them: a deep copy without invisible fields."""
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


def dump(events: Events) -> str:
    return json.dumps(events, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def record(scenario: Any, path: Path) -> Events:
    """Write the scenario's projected output to `path` and return it."""
    events = project(scenario.outputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(events), encoding="utf-8")
    return events


def load(path: Path) -> Events:
    """The golden at `path`, projected again so an older file compares alike."""
    return project(json.loads(path.read_text(encoding="utf-8")))


def _renumber(events: Events) -> Events:
    for index, event in enumerate(events, start=1):
        event["seq"] = index
    return events


DELTAS: Dict[str, Delta] = {}


def apply_deltas(expected: Events, actual: Events,
                 allowed: Iterable[str]) -> Tuple[Events, Events]:
    """Fold transport away, canonicalize under the allowed deltas, renumber."""
    expected, actual = canonical(copy.deepcopy(expected)), canonical(copy.deepcopy(actual))
    for delta_id in allowed:
        expected, actual = DELTAS[delta_id](expected, actual)
    return _renumber(expected), _renumber(actual)


def describe_mismatch(path: Path, expected: Events, actual: Events) -> str:
    """The first event that differs, in transcript form, then the JSON diff."""
    lines = [f"transcript differs from golden {path}"]
    limit = min(len(expected), len(actual))
    first = next((i for i in range(limit) if expected[i] != actual[i]), limit)
    if first < limit:
        lines.append(f"first difference at event {expected[first]['seq']}:")
        lines.append("  expected: " + format_event(expected[first]).replace("\n", "\n  "))
        lines.append("  actual:   " + format_event(actual[first]).replace("\n", "\n  "))
    else:
        lines.append(f"expected {len(expected)} events, got {len(actual)}")
        extra = actual[limit:] or expected[limit:]
        source = "actual" if len(actual) > limit else "expected"
        for event in extra[:3]:
            lines.append(f"  only in {source}: " + format_event(event).replace("\n", "\n  "))

    diff = difflib.unified_diff(
        dump(expected).splitlines(), dump(actual).splitlines(),
        fromfile="golden", tofile="actual", lineterm="", n=2,
    )
    diff_lines = list(diff)
    lines.append("")
    lines.extend(diff_lines[:DIFF_LINES])
    if len(diff_lines) > DIFF_LINES:
        lines.append(f"... {len(diff_lines) - DIFF_LINES} more diff lines")
    return "\n".join(lines)


def assert_golden(scenario: Any, path: Path, allowed: Iterable[str] = ()) -> None:
    """Fail with a transcript-style diff unless the scenario reproduces `path`."""
    if not path.exists():
        raise ScenarioAssertionError(
            f"no golden at {path}; record it with `pytest --update-golden`",
            scenario.transcript,
        )
    expected, actual = apply_deltas(load(path), project(scenario.outputs), allowed)
    if expected == actual:
        return
    raise ScenarioAssertionError(
        describe_mismatch(path, expected, actual), scenario.transcript
    )

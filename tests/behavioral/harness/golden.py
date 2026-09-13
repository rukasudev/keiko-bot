"""Golden transcripts: a scenario's normalized output, pinned to a JSON file.

A golden is the definition of "the admin sees the same thing" (review II.3):
a later run of the same path, on any engine, must reproduce it exactly, except
for the deltas listed in `docs/ux-changes.md` and passed by id in `allowed`.

Component ids never enter a golden. They are the one normalized field the
user cannot see (`action`, a custom_id production code chose), and the new
engine encodes session and revision in every id.
"""
import copy
import difflib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple

from tests.behavioral.harness.errors import ScenarioAssertionError
from tests.behavioral.harness.transcript import format_event

Events = List[Dict[str, Any]]
Delta = Callable[[Events, Events], Tuple[Events, Events]]

INVISIBLE_FIELDS = ("action",)
ERROR_COLOR = 0xFF0000
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


def project(events: Events) -> Events:
    """The events as a golden stores them: a deep copy without invisible fields."""
    return [_strip_invisible(copy.deepcopy(event)) for event in events]


def dump(events: Events) -> str:
    return json.dumps(events, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def record(scenario: Any, path: Path) -> Events:
    """Write the scenario's projected output to `path` and return it."""
    events = project(scenario.outputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(events), encoding="utf-8")
    return events


def load(path: Path) -> Events:
    return json.loads(path.read_text(encoding="utf-8"))


def _renumber(events: Events) -> Events:
    for index, event in enumerate(events, start=1):
        event["seq"] = index
    return events


def _is_notice(event: Dict[str, Any]) -> bool:
    return (
        event.get("actor") == "bot"
        and event.get("kind") == "followup_send"
        and event.get("delete_after") is not None
    )


def _is_ephemeral_error(event: Dict[str, Any]) -> bool:
    embed = event.get("embed") or {}
    return (
        event.get("actor") == "bot"
        and event.get("kind") == "followup_send"
        and bool(event.get("ephemeral"))
        and embed.get("color") == ERROR_COLOR
    )


def _without_notices(expected: Events, actual: Events) -> Tuple[Events, Events]:
    """ux-1: a stale or duplicate click answers with a short self-deleting notice."""
    return expected, [event for event in actual if not _is_notice(event)]


def _without_expiry(expected: Events, actual: Events) -> Tuple[Events, Events]:
    """ux-2: after a recorded timeout the new engine finalizes visibly."""

    def before_timeout(events: Events) -> Events:
        kept = []
        for event in events:
            if event.get("actor") == "user" and event.get("kind") == "timeout":
                break
            kept.append(event)
        return kept

    return before_timeout(expected), before_timeout(actual)


def _error_marker(event: Dict[str, Any]) -> Dict[str, Any]:
    return {"seq": event["seq"], "actor": "bot", "kind": "error",
            "step": event.get("step")}


def _ephemeral_required_error(expected: Events, actual: Events) -> Tuple[Events, Events]:
    """ux-3: the required-options error leaves the public channel for an ephemeral embed."""
    expected = list(expected)
    actual = list(actual)
    for index, event in enumerate(expected):
        if event.get("kind") != "channel_send" or index >= len(actual):
            continue
        if _is_ephemeral_error(actual[index]):
            expected[index] = _error_marker(event)
            actual[index] = _error_marker(actual[index])
    return expected, actual


def _send_before_delete(events: Events) -> Events:
    ordered = list(events)
    index = 0
    while index + 1 < len(ordered):
        first, second = ordered[index], ordered[index + 1]
        if first.get("kind") == "delete" and second.get("kind") == "followup_send":
            ordered[index], ordered[index + 1] = second, first
            index += 2
            continue
        index += 1
    return ordered


def _replace_order(expected: Events, actual: Events) -> Tuple[Events, Events]:
    """ux-4: a message replacement sends the new screen before deleting the old one."""
    return _send_before_delete(expected), _send_before_delete(actual)


def _card_replaced_after_item_added(expected: Events,
                                    actual: Events) -> Tuple[Events, Events]:
    """ux-5: the card that could not be edited is deleted after the final message."""
    kept = []
    for index, event in enumerate(actual):
        previous = actual[index - 1] if index else None
        follows_send = bool(previous) and previous.get("kind") == "followup_send"
        expected_delete = index < len(expected) and expected[index].get("kind") == "delete"
        if event.get("kind") == "delete" and follows_send and not expected_delete:
            continue
        kept.append(event)
    return expected, kept


DELTAS: Dict[str, Delta] = {
    "ux-1": _without_notices,
    "ux-2": _without_expiry,
    "ux-3": _ephemeral_required_error,
    "ux-4": _replace_order,
    "ux-5": _card_replaced_after_item_added,
}


def apply_deltas(expected: Events, actual: Events,
                 allowed: Iterable[str]) -> Tuple[Events, Events]:
    """Canonicalize both sides under the allowed deltas, then renumber."""
    expected, actual = copy.deepcopy(expected), copy.deepcopy(actual)
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

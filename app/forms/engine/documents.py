"""The saved document and the answers, in both directions.

Documents keep the shape the rest of the bot reads today, `{style, values}`
envelopes and composition items with titled entries; a `schema_version`
field, when a migration adds one, is bookkeeping this module skips.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.forms.definitions.schema import Step
from app.forms.engine.session import Answer
from app.forms.engine.summary import ResponseView, responses

BOOKKEEPING = (
    "_id",
    "guild_id",
    "enabled",
    "created_at",
    "updated_at",
    "schema_version",
)


def unwrap(value: Any) -> Any:
    """The machine value inside a `{style, values}` or `{value}` envelope."""
    if isinstance(value, Mapping):
        if "values" in value:
            return value["values"]
        if "value" in value:
            return value.get("_raw_value", value["value"])
    return value


def item_answers(item: Mapping[str, Any]) -> dict[str, Answer]:
    """A stored composition item as the answers of its child session."""
    answers: dict[str, Answer] = {}
    for key, entry in item.items():
        if isinstance(entry, Mapping) and "value" in entry:
            answers[key] = Answer(entry.get("_raw_value", entry["value"]))
        else:
            answers[key] = Answer(entry)
    return answers


def document_answers(document: Mapping[str, Any]) -> dict[str, Answer]:
    """Every setting of a document as an answer, envelopes unwrapped."""
    answers: dict[str, Answer] = {}
    for key, value in document.items():
        if key in BOOKKEEPING:
            continue
        if isinstance(value, Mapping) and value.get("style") == "composition":
            items = value.get("values") or []
            answers[key] = Answer(tuple(item_answers(item) for item in items))
            continue
        answers[key] = Answer(unwrap(value))
    return answers


def document_values(document: Mapping[str, Any]) -> dict[str, Any]:
    """Every setting of a document as its machine value, for rules."""
    return {key: answer.raw for key, answer in document_answers(document).items()}


def _stored(view: ResponseView) -> Any:
    value = view.machine_value
    if isinstance(value, (list, tuple, set)):
        value = list(value)
    if view.style:
        return {"style": view.style, "values": value}
    return value


def to_document(
    steps: Sequence[Step], answers: Mapping[str, Answer], locale: str
) -> dict[str, Any]:
    """The document the answers persist as, enabled, in the v1 shape."""
    document: dict[str, Any] = {"enabled": True}
    for view in responses(steps, answers, locale):
        document[view.key] = _stored(view)
    return document


def items_of(document: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """The stored items of the composition `key`, as dicts."""
    value = document.get(key)
    if isinstance(value, Mapping):
        return [dict(item) for item in (value.get("values") or [])]
    return []

"""The answers as a list of titled values: the review, the panel, the document.

Nothing here is stored. Titles, labels and styles come from the definition at
the moment of rendering, so a session holds machine values only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.forms.definitions.schema import (
    ButtonOptionsSection,
    CardStep,
    CompositionStep,
    MultiPickStep,
    MultiSelectSection,
    Option,
    SingleChoiceStep,
    Step,
    TextStep,
    ValueSelectSection,
)
from app.forms.engine.session import Answer
from app.forms.extensions.transforms import transform


@dataclass(frozen=True)
class ResponseView:
    """One answered value the way a summary lists it."""

    key: str
    title: str
    value: Any
    style: str | None = None
    hidden: bool = False
    raw: Any = None

    @property
    def machine_value(self) -> Any:
        """The value a document stores: the raw one when a label was shown."""
        return self.value if self.raw is None else self.raw

    def as_item_entry(self) -> dict[str, Any]:
        """The shape one field takes inside a stored composition item."""
        entry: dict[str, Any] = {"value": self.value, "title": self.title}
        if self.style:
            entry["style"] = self.style
        if self.hidden:
            entry["hidden"] = True
        if self.raw is not None:
            entry["_raw_value"] = self.raw
        return entry


def option_label(options: Sequence[Option], value: Any, locale: str) -> str | None:
    """The label of `value` among `options`, None when it is not one."""
    for option in options:
        if str(option.value) == str(value):
            return option.label.get(locale)
    return None


def _labels_for(options: Sequence[Option], raw: Any, locale: str) -> Any:
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    labels = [option_label(options, value, locale) or value for value in values]
    return labels if isinstance(raw, (list, tuple)) else labels[0]


def _single_choice_views(
    step: SingleChoiceStep, answer: Answer, locale: str
) -> list[ResponseView]:
    title = step.title.get(locale)
    if step.designs:
        for design in step.designs:
            if design.key == answer.raw:
                return [
                    ResponseView(
                        step.key,
                        title,
                        design.label.get(locale),
                        None,
                        False,
                        answer.raw,
                    )
                ]
        return [ResponseView(step.key, title, answer.raw, None, False, None)]
    if step.styled_values and not step.style:
        labels = _labels_for(step.options, answer.raw, locale)
        return [ResponseView(step.key, title, labels, None, step.hidden, answer.raw)]
    return [ResponseView(step.key, title, answer.raw, step.style, step.hidden, None)]


def _text_views(
    step: TextStep, key: str, answer: Answer, locale: str
) -> list[ResponseView]:
    if key != step.key:
        field = next((f for f in step.fields if f.key == key), None)
        title = field.label.get(locale) if field else key
        return [ResponseView(key, title, answer.raw, None, False, None)]
    if step.fields and answer.raw is None:
        return []
    return [
        ResponseView(
            step.key, step.title.get(locale), answer.raw, step.style, step.hidden
        )
    ]


def _card_hidden_keys(card: CardStep) -> tuple[set[str], set[str]]:
    payload_keys: set[str] = set()
    mode_keys: set[str] = set()
    for section in card.sections:
        state = section.state
        if state.mode:
            mode_keys.add(state.mode)
            payload_keys.update(k for k in (state.title, state.content, state.url) if k)
    return payload_keys, mode_keys


def _card_options(card: CardStep) -> dict[str, tuple[Option, ...]]:
    with_options = (ValueSelectSection, ButtonOptionsSection, MultiSelectSection)
    return {
        section.state.value: tuple(section.options)
        for section in card.sections
        if isinstance(section, with_options) and section.state.value
    }


def _card_field_view(
    card: CardStep, key: str, answer: Answer, locale: str
) -> list[ResponseView]:
    if key == card.key:
        return []
    payload_keys, mode_keys = _card_hidden_keys(card)
    field = next((f for f in card.fields if f.key == key), None)
    if field is None:
        return []
    value, raw = answer.raw, None
    label = option_label(_card_options(card).get(key, ()), value, locale)
    if label is not None:
        value, raw = label, answer.raw
    hidden = bool(
        field.hidden
        or key in payload_keys
        or (key in mode_keys and answer.raw != "custom")
    )
    return [ResponseView(key, field.label.get(locale), value, field.style, hidden, raw)]


def item_entries(
    steps: Sequence[Step], item: Mapping[str, Answer], locale: str
) -> dict[str, dict[str, Any]]:
    """A composition item as its stored dict: `{key: {value, title, ...}}`."""
    return {view.key: view.as_item_entry() for view in responses(steps, item, locale)}


def _composition_views(
    step: CompositionStep, answer: Answer, locale: str
) -> list[ResponseView]:
    items = [item_entries(step.steps, item, locale) for item in (answer.raw or ())]
    return [ResponseView(step.key, step.title.get(locale), items, "composition")]


def _producer(steps: Sequence[Step], key: str) -> tuple[Step, str] | None:
    for step in steps:
        if step.key == key:
            return step, "own"
        if isinstance(step, MultiPickStep):
            if any(select.key == key for select in step.selects):
                return step, "select"
        if isinstance(step, CardStep) and key in {f.key for f in step.fields}:
            return step, "field"
        if isinstance(step, TextStep) and key in {f.key for f in step.fields}:
            return step, "field"
    return None


def _views_for(
    step: Step, role: str, key: str, answer: Answer, locale: str
) -> list[ResponseView]:
    if isinstance(step, MultiPickStep) and role == "select":
        select = next(s for s in step.selects if s.key == key)
        return [ResponseView(key, select.label.get(locale), answer.raw, select.style)]
    if isinstance(step, CardStep):
        return _card_field_view(step, key, answer, locale)
    if isinstance(step, TextStep):
        return _text_views(step, key, answer, locale)
    if isinstance(step, SingleChoiceStep):
        return _single_choice_views(step, answer, locale)
    if isinstance(step, CompositionStep):
        return _composition_views(step, answer, locale)
    if step.kind in ("intro", "info", "review"):
        return []
    return [
        ResponseView(
            step.key, step.title.get(locale), answer.raw, step.style, step.hidden
        )
    ]


def responses(
    steps: Sequence[Step], answers: Mapping[str, Answer], locale: str
) -> tuple[ResponseView, ...]:
    """Every answer of `answers` the way a summary lists it, in answer order."""
    views: list[ResponseView] = []
    for key, answer in answers.items():
        found = _producer(steps, key)
        if found is None:
            continue
        step, role = found
        views.extend(_views_for(step, role, key, answer, locale))
    return tuple(views)


def transformed_value(step: TextStep | CardStep, parts: Mapping[str, Any]) -> Any:
    """The stored value of a step's parts under its transform, if any."""
    rule = transform(step.transform)
    return rule.serialize(parts) if rule else None

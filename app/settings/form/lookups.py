"""Which external services a submitted modal needs, and the value to look up."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.settings.form import events as ev
from app.settings.form.form import steps_for
from app.settings.form.form_state import FormSession
from app.settings.form.form_yaml import (
    CardStep,
    FormDefinition,
    ModalInputSection,
    TextStep,
)
from app.settings.form.responses.transforms import normalizer
from app.settings.form.responses.validations import validator


@dataclass(frozen=True)
class Lookup:
    """The external services a validator needs and the typed value to look up."""

    services: tuple[str, ...]
    value: str


def lookup_for(
    definition: FormDefinition, session: FormSession, event: ev.Event
) -> Lookup | None:
    """The lookup an answered modal needs before `decide`, None when it needs none."""
    if not isinstance(event, ev.Answered) or not isinstance(event.payload, Mapping):
        return None
    inputs = event.payload.get("inputs")
    if not inputs:
        return None
    steps = steps_for(definition, session.mode)
    step = next(
        (candidate for candidate in steps if candidate.key == session.cursor), None
    )
    if isinstance(step, CardStep) and event.step_key.startswith("section:"):
        return _card_lookup(step, event.step_key, inputs)
    if isinstance(step, TextStep) and step.key == event.step_key:
        value = str(inputs[0])
        if step.lowercase:
            value = value.lower()
        named = tuple(path.split(".", 1)[0] for path in step.lookup_answers.values())
        return _lookup(step.validation, _normalized(step.normalize, value), named)
    return None


def _card_lookup(
    card: CardStep, step_key: str, inputs: Sequence[object]
) -> Lookup | None:
    index = int(step_key.split(":", 1)[1])
    if not 0 <= index < len(card.sections):
        return None
    section = card.sections[index]
    if not isinstance(section, ModalInputSection):
        return None
    key = section.state.value
    fields = section.modal.fields
    position = next(
        (index for index, field in enumerate(fields) if field.key == key), 0
    )
    value = str(inputs[position]) if position < len(inputs) else ""
    field = fields[position] if position < len(fields) else None
    named = tuple(path.split(".", 1)[0] for path in section.lookup_answers.values())
    return _lookup(
        section.modal.validation,
        _normalized(field.normalize if field else None, value),
        named,
    )


def _normalized(name: str | None, value: str) -> str:
    normalize = normalizer(name)
    return normalize(value) if normalize else value


def _lookup(name: str | None, value: str, named: tuple[str, ...] = ()) -> Lookup | None:
    check = validator(name)
    needs = check.needs if check else ()
    services = [need.split(":", 1)[1] for need in needs if need.startswith("external:")]
    services += [service for service in named if service not in services]
    return Lookup(tuple(services), value) if services else None

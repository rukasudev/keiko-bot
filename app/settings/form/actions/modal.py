"""A modal with text inputs, or a file input, validated before it is answered."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from app.constants import KeikoIcons
from app.settings.form.actions import Refusal
from app.settings.form.actions.action import (
    Context,
    back_button,
    cancel_button,
    description_of,
    footer_of,
    other_items,
)
from app.settings.form.components import (
    Button,
    Card,
    CardHeader,
    FileInput,
    Input,
    Screen,
    SectionView,
    TextInputs,
)
from app.settings.form.copy import text
from app.settings.form.form_state import Answer, FormSession
from app.settings.form.form_yaml import TextField, TextStep
from app.settings.form.responses.summary import step_title
from app.settings.form.responses.transforms import normalizer, transform
from app.settings.form.responses.validations import ValidationContext, validator

DEFAULT_MAX_LENGTH = 40


def _labels(step: TextStep, locale: str) -> list[str]:
    counts: dict[str, int] = {}
    if step.enumerate:
        for field in step.fields:
            name = field.label.get(locale)
            counts[name] = counts.get(name, 0) + 1
    seen: dict[str, int] = {}
    labels = []

    for field in step.fields:
        name = field.label.get(locale)
        if counts.get(name, 0) > 1:
            seen[name] = seen.get(name, 0) + 1
            name = f"{name} #{seen[name]}"
        labels.append(name)
    return labels


def _defaults(step: TextStep, session: FormSession) -> list[str | None]:
    """Per input: what was typed and refused, the value already answered, or nothing."""
    refused = session.refused_on(step.key)
    if refused is not None and refused.inputs:
        return list(refused.typed_into(max(len(step.fields), 1)))
    answer = session.answers.get(step.key)
    if answer is None or answer.raw is None and not answer.parts:
        return [None] * max(len(step.fields), 1)
    if not step.fields:
        return [str(answer.raw)]
    values = list(answer.part("__inputs__") or [])
    return [
        values[index] if index < len(values) else None
        for index in range(len(step.fields))
    ]


def _input(
    field: TextField, label: str, step: TextStep, locale: str, default: str | None
) -> Input:
    declared = field.default.get(locale) if field.default else None
    placeholder = field.placeholder.get(locale) if field.placeholder else None

    if field.description and not placeholder:
        placeholder = field.description.get(locale)
    return Input(
        label=label,
        key=field.key,
        placeholder=placeholder or declared,
        default=default if default is not None else declared,
        required=field.required,
        max_length=field.max_length or step.max_length,
        multiline=step.multiline,
    )


def render(step: Any, session: FormSession, context: Context) -> Screen:
    """The modal: one input per field, or one file input."""
    locale = context.locale
    titled = step_title(step, locale, context.scope)
    if step.input == "file":
        title = step.modal_title.get(locale) if step.modal_title else titled
        component = FileInput(
            step.key, text("buttons.file-upload.label", locale) or "Image"
        )
        return Screen(modal_title=title, components=(component,), flavour="modal")
    defaults = _defaults(step, session)
    inputs: tuple[Input, ...]

    if not step.fields:
        declared = step.placeholder.get(locale) if step.placeholder else None
        inputs = (
            Input(
                label=step.label.get(locale) if step.label else titled,
                placeholder=declared,
                default=defaults[0],
                required=step.required or True,
                max_length=step.max_length,
                multiline=step.multiline,
            ),
        )
    else:
        inputs = tuple(
            _input(field, label, step, locale, default)
            for field, label, default in zip(
                step.fields, _labels(step, locale), defaults
            )
        )
    return Screen(
        modal_title=titled,
        components=(TextInputs(step.key, inputs),),
        flavour="modal",
    )


def refused_screen(step: Any, session: FormSession, context: Context) -> Screen:
    """The refused step as a one-field card: what was typed and its Edit."""
    locale = context.locale
    refused = session.refused_on(step.key)
    typed = refused.inputs if refused is not None else ()
    titled = step_title(step, locale, context.scope)
    name = step.label.get(locale) if step.label else titled
    lines = (
        [f"> {typed[0]}"]
        if not step.fields and typed and typed[0]
        else [
            f"> **{label}:** {value}"
            for label, value in zip(_labels(step, locale), typed)
            if value
        ]
    )

    section = SectionView(
        0,
        step.key,
        f"{step.emoji} **{name}**".strip(),
        tuple(lines),
        (Button(text("buttons.summary-card.edit", locale), "section:0"),),
    )
    header = CardHeader(
        titled,
        description_of(step, session, context),
        thumbnail=KeikoIcons.IMAGE_01,
    )
    buttons = (back_button(locale),) if context.can_go_back else ()
    return Screen(
        components=(Card(step.key, header, (section,)),),
        buttons=(*buttons, cancel_button(locale)),
        flavour="components_v2",
        layout_footer=footer_of(step, locale),
    )


def _validate(
    step: TextStep, value: Any, session: FormSession, context: Context
) -> Refusal | None:
    check = validator(step.validation)
    if check is None:
        return None
    error = check.check(
        value,
        ValidationContext(
            answers=session.values(),
            items=other_items(session, context.items),
            external=context.external,
        ),
    )
    return Refusal(error) if error else None


def _looked_up(step: TextStep, context: Context) -> dict[str, Answer]:
    found: dict[str, Answer] = {}
    for key, path in step.lookup_answers.items():
        service, _, field = path.partition(".")
        found[key] = Answer(context.external.get(service, {}).get(field))
    return found


def _scalar(step: TextStep, values: Sequence[str]) -> Any:
    value = values[0] if values else ""
    rule = transform(step.transform)
    if rule and rule.value_key == step.key:
        transformed = rule.serialize({step.key: value})
        return transformed if transformed is not None else value
    return value


def parse(
    step: Any, payload: Any, session: FormSession, context: Context
) -> Mapping[str, Answer] | Refusal:
    """Text inputs in order, or a file url, into answers."""
    if step.input == "file":
        url = payload.get("url") if isinstance(payload, Mapping) else payload
        if not url:
            return Refusal("selection-required", target=step.key)
        return {step.key: Answer(url)}
    raw_values = payload.get("inputs") if isinstance(payload, Mapping) else [payload]
    values = [str(value) for value in (raw_values or [])]
    typed = tuple(values)

    if step.lowercase:
        values = [value.lower() for value in values]
    normalize = normalizer(step.normalize)
    if normalize:
        values = [normalize(value) for value in values]
    if not step.fields:
        refusal = _validate(step, values[0] if values else "", session, context)

        if refusal:
            return replace(refusal, inputs=typed, target=step.key)
        return {step.key: Answer(_scalar(step, values)), **_looked_up(step, context)}
    answers = _fields_answers(step, values, session, context)
    if isinstance(answers, Refusal):
        return replace(answers, inputs=typed, target=step.key)
    return answers


def _fields_answers(
    step: TextStep, values: list[str], session: FormSession, context: Context
) -> Mapping[str, Answer] | Refusal:
    keyed: dict[str, Answer] = {}
    concat: list[str] = []

    for field, typed in zip(step.fields, values):
        normalize_field = normalizer(field.normalize)
        value = normalize_field(typed) if normalize_field else typed

        if field.key:
            keyed[field.key] = Answer(value)
        elif value:
            concat.append(value)
    joined = ";".join(concat) if concat else None
    refusal = _validate(step, joined or values[0] if values else "", session, context)

    if refusal:
        return refusal
    parts = {"__inputs__": tuple(values)}
    return {**keyed, step.key: Answer(joined, parts), **_looked_up(step, context)}

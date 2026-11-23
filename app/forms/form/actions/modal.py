"""A modal with one text input, checked before it is answered."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from app.constants import KeikoIcons
from app.forms.form.actions import Refusal
from app.forms.form.actions.action import (
    Context,
    back_button,
    cancel_button,
    description_of,
    footer_of,
    other_items,
)
from app.forms.form.components import (
    Button,
    Card,
    CardHeader,
    Input,
    Screen,
    SectionView,
    TextInputs,
)
from app.forms.form.copy import text
from app.forms.form.form_state import Answer, FormSession
from app.forms.form.form_yaml import TextStep
from app.forms.form.responses.summary import step_title
from app.forms.form.responses.transforms import normalizer, transform
from app.forms.form.responses.transforms import told as normalizer_notices
from app.forms.form.responses.validations import ValidationContext, validator


def _typed(step: TextStep, session: FormSession) -> str | None:
    refused = session.refused_on(step.key)
    if refused is not None and refused.inputs:
        return refused.typed_into(1)[0]
    answer = session.answers.get(step.key)
    if answer is None or answer.raw is None and not answer.parts:
        return None
    return str(answer.raw)


def render(step: Any, session: FormSession, context: Context) -> Screen:
    """The modal: one text input, holding what was typed or answered before."""
    locale = context.locale
    titled = step_title(step, locale, context.scope)
    typed = Input(
        label=step.label.get(locale) if step.label else titled,
        placeholder=step.placeholder.get(locale) if step.placeholder else None,
        default=_typed(step, session),
        max_length=step.max_length,
    )
    return Screen(
        modal_title=titled,
        components=(TextInputs(step.key, (typed,)),),
        flavour="modal",
    )


def refused_screen(step: Any, session: FormSession, context: Context) -> Screen:
    """The refused step as a one-field card: what was typed and its Edit."""
    locale = context.locale
    refused = session.refused_on(step.key)
    typed = refused.inputs if refused is not None else ()
    titled = step_title(step, locale, context.scope)
    name = step.label.get(locale) if step.label else titled
    lines = [f"> {typed[0]}"] if typed and typed[0] else []

    section = SectionView(
        0,
        step.key,
        f"{step.emoji} **{name}**".strip(),
        tuple(lines),
        (Button(text("buttons.edit.label", locale), "section:0"),),
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
    declared = step.validation
    check = validator(declared.name) if declared else None
    if declared is None or check is None:
        return None
    error = check.check(
        value,
        ValidationContext(
            answers=session.values(),
            items=other_items(session, context.items),
            external=context.external,
            params=declared.params,
        ),
    )
    return Refusal(error, check.facts) if error else None


def _looked_up(step: TextStep, context: Context) -> dict[str, Answer]:
    found: dict[str, Answer] = {}
    for key, path in step.lookup_answers.items():
        service, _, field = path.partition(".")
        found[key] = Answer(context.external.get(service, {}).get(field))
    return found


def _scalar(step: TextStep, values: Sequence[str]) -> Any:
    value = values[0] if values else ""
    rule = transform(step.transform.name) if step.transform else None
    if rule and rule.value_key == step.key:
        transformed = rule.serialize({step.key: value})
        return transformed if transformed is not None else value
    return value


def told(step: TextStep, payload: Any) -> tuple[str, ...]:
    """The responses the step's normalizer tells about what its modal typed."""
    typed = payload.get("inputs") if isinstance(payload, Mapping) else [payload]
    name = step.normalize.name if step.normalize else None
    values = list(typed or [])
    return normalizer_notices([name] * len(values), values)


def parse(
    step: Any, payload: Any, session: FormSession, context: Context
) -> Mapping[str, Answer] | Refusal:
    """The typed text, normalized, checked and transformed, into its answer."""
    raw_values = payload.get("inputs") if isinstance(payload, Mapping) else [payload]
    values = [str(value) for value in (raw_values or [])]
    typed = tuple(values)
    normalize = normalizer(step.normalize.name if step.normalize else None)

    if normalize:
        values = [normalize.apply(value) for value in values]
    refusal = _validate(step, values[0] if values else "", session, context)
    if refusal:
        return replace(refusal, inputs=typed, target=step.key)
    return {step.key: Answer(_scalar(step, values)), **_looked_up(step, context)}

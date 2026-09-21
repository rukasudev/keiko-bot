"""A grid of option buttons, or a gallery of designs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import Style
from app.settings.form.actions import Refusal
from app.settings.form.actions.action import (
    RenderContext,
    back_button,
    cancel_button,
    confirm_button,
    design_gallery,
    label,
    step_buttons,
    step_screen,
)
from app.settings.form.components import (
    Button,
    Choice,
    ChoiceOption,
    Field,
    Screen,
)
from app.settings.form.form_state import Answer, FormSession
from app.settings.form.form_yaml import SingleChoiceStep


def _selected(step: SingleChoiceStep, session: FormSession) -> list[str]:
    answer = session.answers.get(step.key)
    if answer is None or answer.raw is None:
        return []
    raw = answer.raw
    return [str(value) for value in (raw if isinstance(raw, (list, tuple)) else [raw])]


def _gallery(step: SingleChoiceStep, context: RenderContext) -> Screen:
    locale = context.locale
    gallery = design_gallery(step.key, step.designs, context)
    buttons: list[Button] = []

    if context.can_go_back:
        buttons.append(back_button(locale))
    buttons.append(cancel_button(locale))
    return Screen(
        components=(gallery,), buttons=tuple(buttons), flavour="components_v2"
    )


def render(step: Any, session: FormSession, context: RenderContext) -> Screen:
    """The options as buttons under the step embed, or the design gallery."""
    if step.designs:
        return _gallery(step, context)
    locale = context.locale
    selected = _selected(step, session)
    options = tuple(
        ChoiceOption(
            label=option.label.get(locale),
            value=str(option.value),
            style=option.style or "secondary",
            selected=str(option.value) in selected,
        )
        for option in step.options
    )
    fields = tuple(
        Field(f":flying_disc: {label('selected', locale)} #{index}", option.label)
        for index, option in enumerate(
            (option for option in options if option.selected), start=1
        )
    )
    own = () if step.auto_confirm else (confirm_button(locale),)

    return step_screen(
        step,
        session,
        context,
        components=(Choice(step.key, options, step.auto_confirm),),
        buttons=step_buttons(locale, context, *own),
        fields=fields,
    )


def _option_value(step: SingleChoiceStep, value: Any) -> Any:
    for option in step.options:
        if str(option.value) == str(value):
            return option.value
    return None


def parse(
    step: Any, payload: Any, session: FormSession, context: RenderContext
) -> Mapping[str, Answer] | Refusal:
    """The chosen option value, typed as the definition declares it."""
    if step.designs:
        if any(design.key == payload for design in step.designs):
            return {step.key: Answer(payload)}
        return Refusal("selection-required")
    chosen = payload if payload is not None else session.raw(step.key)
    values = chosen if isinstance(chosen, (list, tuple)) else [chosen]
    typed = [_option_value(step, value) for value in values if value is not None]
    typed = [value for value in typed if value is not None]

    if not typed:
        if step.required:
            return Refusal("command-required-interaction")
        return {step.key: Answer([])}
    if step.unique or len(typed) == 1:
        return {step.key: Answer(typed[0])}
    return {step.key: Answer(typed)}


BACKGROUND = Style.BACKGROUND_COLOR

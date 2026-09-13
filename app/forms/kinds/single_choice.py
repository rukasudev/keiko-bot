"""A grid of option buttons, or a gallery of designs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import Style
from app.forms.definitions.schema import SingleChoiceStep
from app.forms.engine.screen import (
    Button,
    Choice,
    ChoiceOption,
    DesignCard,
    Field,
    Gallery,
    Screen,
)
from app.forms.engine.session import Answer, FormSession
from app.forms.extensions.copy import text
from app.forms.kinds import Refusal
from app.forms.kinds.common import (
    back_button,
    cancel_button,
    confirm_button,
    label,
    step_buttons,
    step_screen,
)
from app.forms.kinds.context import RenderContext


def _selected(step: SingleChoiceStep, session: FormSession) -> list[str]:
    answer = session.answers.get(step.key)
    if answer is None or answer.raw is None:
        return []
    raw = answer.raw
    return [str(v) for v in (raw if isinstance(raw, (list, tuple)) else [raw])]


def _gallery(step: SingleChoiceStep, context: RenderContext) -> Screen:
    locale = context.locale
    designs = tuple(
        DesignCard(
            key=design.key,
            label=design.label.get(locale),
            description=design.description.get(locale),
            preview_url=context.previews.get(design.key),
        )
        for design in step.designs
    )
    gallery = Gallery(
        step_key=step.key,
        header=text("buttons.components.design-select.header", locale),
        designs=designs,
        footer=text("buttons.components.design-select.footer", locale),
        select_label=label("select", locale),
    )
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
        for index, option in enumerate((o for o in options if o.selected), start=1)
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
    typed = [_option_value(step, v) for v in values if v is not None]
    typed = [v for v in typed if v is not None]
    if not typed:
        if step.required:
            return Refusal("command-required-interaction")
        return {step.key: Answer([])}
    if step.unique or len(typed) == 1:
        return {step.key: Answer(typed[0])}
    return {step.key: Answer(typed)}


BACKGROUND = Style.BACKGROUND_COLOR

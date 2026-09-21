"""A read-only screen with titled paragraphs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.settings.form.actions.action import (
    RenderContext,
    confirm_button,
    step_buttons,
    step_screen,
)
from app.settings.form.components import Field, Screen
from app.settings.form.form_state import Answer, FormSession


def render(step: Any, session: FormSession, context: RenderContext) -> Screen:
    """The embed with its paragraphs as fields, then Confirm, Back, Cancel."""
    locale = context.locale
    fields = tuple(
        Field(paragraph.title, paragraph.message)
        for paragraph in (step.fields.get(locale) if step.fields else ())
    )
    return step_screen(
        step,
        session,
        context,
        fields=fields,
        buttons=step_buttons(locale, context, confirm_button(locale)),
    )


def parse(
    step: Any, payload: Any, session: FormSession, context: RenderContext
) -> Mapping[str, Answer]:
    """Reading a screen answers nothing."""
    return {}

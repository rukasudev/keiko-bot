"""The first screen: what the feature does and every setting it will ask for."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.constants import Emojis
from app.settings.form.actions.action import (
    RenderContext,
    cancel_button,
    confirm_button,
    step_screen,
)
from app.settings.form.components import Screen
from app.settings.form.copy import text
from app.settings.form.form_state import Answer, FormSession
from app.settings.form.form_yaml import CardStep, CompositionStep, Step

SILENT_KINDS = ("intro", "info", "review")


def settings_list(steps: Sequence[Step], locale: str) -> dict[str, str]:
    """Title to description of every setting the form asks, cards by field."""
    listed: dict[str, str] = {}
    for step in steps:
        if step.hidden:
            continue
        if isinstance(step, CompositionStep):
            if step.when is None:
                listed.update(settings_list(step.steps, locale))
            continue
        if isinstance(step, CardStep):
            for field in step.fields:
                if not field.hidden and field.description:
                    listed[field.label.get(locale)] = field.description.get(locale)
            continue
        if step.kind in SILENT_KINDS:
            continue
        listed[step.title.get(locale)] = step.description.get(locale)
    return listed


def settings_text(steps: Sequence[Step], locale: str) -> str:
    """The settings list as the intro appends it to its description."""
    heading = text("commands.resume.settings", locale)
    body = f"\n\n:pencil: **{heading}**\n"
    for title, description in settings_list(steps, locale).items():
        body += f"\n{Emojis.FRISBEE_EMOJI} **{title}**: {description}"
    return body


def render(step: Any, session: FormSession, context: RenderContext) -> Screen:
    """The intro embed with the settings list and Confirm / Cancel."""
    description = step.description.get(context.locale) + settings_text(
        context.definition.steps, context.locale
    )
    return step_screen(
        step,
        session,
        context,
        buttons=(confirm_button(context.locale), cancel_button(context.locale)),
        description=description,
    )


def parse(
    step: Any, payload: Any, session: FormSession, context: RenderContext
) -> Mapping[str, Answer]:
    """Confirming the intro answers nothing."""
    return {}

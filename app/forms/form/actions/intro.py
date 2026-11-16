"""The first screen: what the feature does and every setting it will ask for."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.constants import Emojis
from app.forms.form.actions.action import (
    Context,
    cancel_button,
    confirm_button,
    description_of,
    step_screen,
)
from app.forms.form.components import Screen
from app.forms.form.copy import text
from app.forms.form.form_state import Answer, FormSession
from app.forms.form.form_yaml import (
    CardStep,
    ContentStep,
    ListStep,
    Step,
    listed_on_cards,
)
from app.forms.form.responses.summary import labelled


def settings_list(steps: Sequence[Step], locale: str) -> dict[str, str]:
    """Title to one line of every setting the form asks, cards by field, a list once."""
    listed: dict[str, str] = {}
    on_cards = listed_on_cards(steps)
    for step in steps:
        if not isinstance(step, ContentStep) or step.hidden:
            continue
        if step.appears_in == "panel":
            continue
        if isinstance(step, ListStep) and step.key in on_cards:
            listed[step.title.get(locale)] = step.description.get(locale)
            continue
        if isinstance(step, ListStep):
            if step.when is None:
                listed.update(settings_list(step.steps, locale))
            continue
        if isinstance(step, CardStep):
            for field in step.fields:
                if not field.hidden and field.description:
                    label = step.label_of(field).get(locale)
                    listed[label] = field.description.get(locale)
            continue
        listed[step.title.get(locale)] = step.description.get(locale)
    return listed


def settings_text(steps: Sequence[Step], locale: str) -> str:
    """The settings list as the intro appends it to its description."""
    heading = text("texts.settings_heading", locale)
    body = f"\n\n:pencil: **{heading}**\n"
    for title, description in settings_list(steps, locale).items():
        body += f"\n{Emojis.FRISBEE_EMOJI} {labelled(title, description)}"
    return body


def render(step: Any, session: FormSession, context: Context) -> Screen:
    """The intro embed with the settings list and Confirm / Cancel."""
    description = description_of(step, session, context) + settings_text(
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
    step: Any, payload: Any, session: FormSession, context: Context
) -> Mapping[str, Answer]:
    """Confirming the intro answers nothing."""
    return {}

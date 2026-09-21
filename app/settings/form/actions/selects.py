"""Native selects: one channel, role or member, or several selects at once."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import ViewConstants as view_constants
from app.settings.form.actions import Refusal
from app.settings.form.actions.action import (
    RenderContext,
    confirm_button,
    step_buttons,
    step_screen,
)
from app.settings.form.components import Picker, Screen
from app.settings.form.copy import text
from app.settings.form.form_state import Answer, FormSession
from app.settings.form.form_yaml import MultiPickStep

PICKER_KIND = {"channel_pick": "channel", "role_pick": "role", "user_pick": "user"}
SELECT_KIND = {"channels": "channel", "roles": "role", "available_roles": "role"}


def _ids(value: Any) -> tuple[str, ...]:
    if value in (None, "", []):
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(value) for value in value)
    return (str(value),)


def render(step: Any, session: FormSession, context: RenderContext) -> Screen:
    """The select or selects with the current picks, Confirm, Back and Cancel."""
    locale = context.locale
    if isinstance(step, MultiPickStep):
        pickers = tuple(
            Picker(
                step_key=step.key,
                kind=SELECT_KIND[select.type],  # type: ignore[arg-type]
                placeholder=select.placeholder.get(locale),
                selected=_ids(session.raw(select.key)),
                unique=False,
                slot=select.key,
                available_only=select.type == "available_roles",
            )
            for select in step.selects
        )
    else:
        kind = PICKER_KIND[step.kind]
        pickers = (
            Picker(
                step_key=step.key,
                kind=kind,  # type: ignore[arg-type]
                placeholder=text(
                    f"buttons.components.select.{kind}-placeholder", locale
                ),
                selected=_ids(session.raw(step.key)),
                unique=step.unique,
                available_only=step.kind == "role_pick" and step.available,
            ),
        )
    return step_screen(
        step,
        session,
        context,
        components=pickers,
        buttons=step_buttons(locale, context, confirm_button(locale)),
    )


def _stored(values: tuple[str, ...]) -> Any:
    return values[0] if len(values) == 1 else list(values)


def parse(
    step: Any, payload: Any, session: FormSession, context: RenderContext
) -> Mapping[str, Answer] | Refusal:
    """The ids picked: one scalar, several as a list, none when nothing."""
    if isinstance(step, MultiPickStep):
        answers: dict[str, Answer] = {}
        picked = False
        for select in step.selects:
            draft = session.answers.get(select.key)
            values = _ids(draft.raw) if draft else ()
            picked = picked or bool(values)
            answers[select.key] = Answer(_stored(values))
        if step.required and not picked:
            return Refusal(
                "selection-required",
                plain=True,
                delete_after=view_constants.ACTION_NOTICE_SECONDS,
            )
        return answers
    draft = session.answers.get(step.key)
    values = (
        _ids(payload) if payload is not None else (_ids(draft.raw) if draft else ())
    )

    if step.required and not values:
        return Refusal(
            "selection-required",
            plain=True,
            delete_after=view_constants.ACTION_NOTICE_SECONDS,
        )
    return {step.key: Answer(_stored(values) if values else [])}

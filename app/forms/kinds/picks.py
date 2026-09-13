"""Native selects: one channel, role or member, or several selects at once."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.forms.definitions.schema import MultiPickStep
from app.forms.engine.screen import Picker, Screen
from app.forms.engine.session import Answer, FormSession
from app.forms.extensions.copy import text
from app.forms.kinds import Refusal
from app.forms.kinds.common import confirm_button, step_buttons, step_screen
from app.forms.kinds.context import RenderContext

PICKER_KIND = {"channel_pick": "channel", "role_pick": "role", "user_pick": "user"}
SELECT_KIND = {"channels": "channel", "roles": "role", "available_roles": "role"}


def _ids(value: Any) -> tuple[str, ...]:
    if value in (None, "", []):
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value),)


def _placeholder(kind: str, locale: str) -> str:
    return text(f"buttons.components.select.{kind}-placeholder", locale)


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
                placeholder=_placeholder(kind, locale),
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
        for select in step.selects:
            draft = session.answers.get(select.key)
            values = _ids(draft.raw) if draft else ()
            answers[select.key] = Answer(_stored(values))
        return answers
    draft = session.answers.get(step.key)
    values = (
        _ids(payload) if payload is not None else (_ids(draft.raw) if draft else ())
    )
    if step.required and not values:
        return Refusal("selection-required", plain=True)
    return {step.key: Answer(_stored(values) if values else [])}

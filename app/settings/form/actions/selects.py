"""Native selects: one channel, role or member, or several selects at once."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import ViewConstants as view_constants
from app.settings.form.actions import Refusal
from app.settings.form.actions.action import (
    Context,
    confirm_button,
    label,
    step_buttons,
    step_screen,
)
from app.settings.form.components import Button, Picker, Screen
from app.settings.form.copy import text
from app.settings.form.form_state import Answer, FormSession
from app.settings.form.form_yaml import MultiPickStep, Select

PICKER_KIND = {"channel_pick": "channel", "role_pick": "role", "user_pick": "user"}
SELECT_KIND = {"channels": "channel", "roles": "role", "available_roles": "role"}


def picked_ids(value: Any) -> tuple[str, ...]:
    """The ids a stored pick holds: none, one or several."""
    if value in (None, "", []):
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(value) for value in value)
    return (str(value),)


def select_picker(
    step_key: str, select: Select, picked: Any, locale: str, required: bool = False
) -> Picker:
    """The native select a declared `Select` draws, with what it holds picked."""
    return Picker(
        step_key=step_key,
        kind=SELECT_KIND[select.type],  # type: ignore[arg-type]
        placeholder=select.placeholder.get(locale),
        selected=picked_ids(picked),
        unique=False,
        slot=select.key,
        available_only=select.type == "available_roles",
        required=required,
    )


def render(step: Any, session: FormSession, context: Context) -> Screen:
    """The select or selects with the current picks, Confirm, Back and Cancel."""
    locale = context.locale
    if isinstance(step, MultiPickStep):
        pickers = tuple(
            select_picker(step.key, select, session.raw(select.key), locale)
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
                selected=picked_ids(session.raw(step.key)),
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


def part_screen(
    step: MultiPickStep, part: str, session: FormSession, context: Context
) -> Screen:
    """One select of a multi-select, with a way back and nothing else."""
    locale = context.locale
    select = next(
        (candidate for candidate in step.selects if candidate.key == part),
        step.selects[0],
    )
    picker = select_picker(step.key, select, session.raw(select.key), locale)
    return step_screen(
        step,
        session,
        context,
        components=(picker,),
        buttons=(Button(label("back", locale), "picker_back"),),
    )


def stored_ids(values: tuple[str, ...]) -> Any:
    """The ids as a document keeps them: one as itself, several as a list."""
    return values[0] if len(values) == 1 else list(values)


def parse(
    step: Any, payload: Any, session: FormSession, context: Context
) -> Mapping[str, Answer] | Refusal:
    """The ids picked: one scalar, several as a list, none when nothing."""
    if isinstance(step, MultiPickStep):
        answers: dict[str, Answer] = {}
        picked = False
        for select in step.selects:
            draft = session.answers.get(select.key)
            values = picked_ids(draft.raw) if draft else ()
            picked = picked or bool(values)
            answers[select.key] = Answer(stored_ids(values))
        if step.required and not picked:
            return Refusal(
                "fill-one-field",
                plain=True,
                delete_after=view_constants.ACTION_NOTICE_SECONDS,
            )
        return answers
    draft = session.answers.get(step.key)
    values = (
        picked_ids(payload)
        if payload is not None
        else (picked_ids(draft.raw) if draft else ())
    )

    if step.required and not values:
        return Refusal(
            "selection-required",
            plain=True,
            delete_after=view_constants.ACTION_NOTICE_SECONDS,
        )
    return {step.key: Answer(stored_ids(values) if values else [])}

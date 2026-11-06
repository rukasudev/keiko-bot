"""The last screen: a card of every answer, then Preview, Add, Remove, Confirm."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from app.constants import KeikoIcons
from app.settings.form.actions.action import (
    Context,
    apply_tokens,
    cancel_button,
    caption,
    confirm_button,
    description_of,
    footer_of,
    label,
    title_of,
)
from app.settings.form.components import Button, Panel, PanelGroup, Screen
from app.settings.form.form_state import Answer, FormSession
from app.settings.form.form_yaml import Step, TextStep
from app.settings.form.responses.responses import to_document
from app.settings.form.responses.styles import format_value
from app.settings.form.responses.summary import (
    every_setting_has_a_button,
    kept_items,
    labelled,
    panel_groups,
    panel_rows,
)


def _item_count(session: FormSession, context: Context) -> int | None:
    composition = context.definition.composition
    if composition is None or not composition.in_setup:
        return None
    answer = session.answers.get(composition.key)
    return len(answer.raw or ()) if answer else 0


def _button(key: str, action: str, emoji: str, locale: str) -> Button:
    return Button(
        label(key, locale), action, "secondary", emoji, description=caption(key, locale)
    )


def _buttons(
    step: Any,
    session: FormSession,
    context: Context,
    covered: bool,
    groups: Sequence[PanelGroup],
) -> tuple[Button, ...]:
    locale = context.locale
    buttons: list[Button] = []
    count = _item_count(session, context)
    composition = context.definition.composition
    actions = {part.action for group in groups for part in group.parts}
    actions |= {button.action for group in groups for button in group.actions}

    if step.preview and not any(
        action.startswith("aside:preview:") for action in actions
    ):
        buttons.append(_button("preview", "aside:preview", "👁️", locale))
    if not covered:
        buttons.append(_button("edit", "edit", "📝", locale))
    if composition is not None and count is not None:
        if f"add:{composition.key}" not in actions:
            if count < composition.items.max:
                buttons.append(_button("add", "add", "➕", locale))
            if count > kept_items(composition):
                buttons.append(_button("remove", "remove", "🗑️", locale))
    return (*buttons, confirm_button(locale), cancel_button(locale))


def _looked_up(step: Any, session: FormSession, locale: str) -> PanelGroup | None:
    """The step's own lines, read from answers the document never keeps."""
    lines = []
    for line in step.lines:
        value = session.raw(line.value_key)
        if value in (None, "", [], (), {}):
            continue
        shown = (
            format_value(value, line.value_format, locale)
            if line.value_format
            else str(value)
        )
        lines.append(f"{line.emoji} {labelled(line.label.get(locale), str(shown))}")
    return PanelGroup(None, "", tuple(lines)) if lines else None


def _with_media(
    groups: Sequence[PanelGroup], steps: Sequence[Step], session: FormSession
) -> tuple[PanelGroup, ...]:
    pictures = {
        step.key: str(session.raw(step.media_key) or "")
        for step in steps
        if isinstance(step, TextStep) and step.media_key
    }
    return tuple(
        replace(group, media=pictures[group.key])
        if group.key in pictures and not group.media
        else group
        for group in groups
    )


def render(step: Any, session: FormSession, context: Context) -> Screen:
    """The review card: every answer in blocks with their Edit, actions below."""
    locale = context.locale
    document = to_document(context.steps, session.answers, locale)
    rows = [
        replace(row, group_screen=False, group_footer="")
        for row in panel_rows(context.definition, document, locale)
    ]
    title = title_of(step, locale)
    groups = panel_groups(rows, title, locale)
    covered = every_setting_has_a_button(
        context.definition, document, groups, locale, rows
    )
    looked_up = _looked_up(step, session, locale)

    groups = _with_media(groups, context.steps, session)
    if looked_up is not None:
        groups = (*groups, looked_up)
    info = apply_tokens(step.info.get(locale), session, context) if step.info else ""
    panel = Panel(
        title=title,
        intro=description_of(step, session, context),
        groups=groups,
        thumbnail=KeikoIcons.IMAGE_03,
        edit_label=label("edit", locale),
        info=info.rstrip(),
    )
    return Screen(
        components=(panel,),
        buttons=_buttons(step, session, context, covered, groups),
        flavour="components_v2",
        layout_footer=footer_of(step, locale),
    )


def parse(
    step: Any, payload: Any, session: FormSession, context: Context
) -> Mapping[str, Answer]:
    """Confirming the review answers nothing; it commits."""
    return {}

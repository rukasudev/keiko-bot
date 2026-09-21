"""The last screen: a card of every answer, then Add, Remove, Preview, Confirm."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.constants import KeikoIcons
from app.settings.form import manager
from app.settings.form.actions.action import (
    RenderContext,
    cancel_button,
    caption,
    confirm_button,
    description_of,
    footer_of,
    label,
    title_of,
)
from app.settings.form.components import Button, Panel, Screen
from app.settings.form.form_state import Answer, FormSession
from app.settings.form.responses.responses import to_document


def _item_count(session: FormSession, context: RenderContext) -> int | None:
    composition = context.definition.composition
    if composition is None:
        return None
    answer = session.answers.get(composition.key)
    return len(answer.raw or ()) if answer else 0


def _has_edit(target: str, keyed: set[str]) -> bool:
    return target in keyed or target.split("$", 1)[0] in keyed


def _button(key: str, action: str, emoji: str, locale: str) -> Button:
    return Button(
        label(key, locale), action, "secondary", emoji, description=caption(key, locale)
    )


def _buttons(
    step: Any, session: FormSession, context: RenderContext, covered: bool
) -> tuple[Button, ...]:
    locale = context.locale
    buttons: list[Button] = [] if covered else [_button("edit", "edit", "📝", locale)]
    count = _item_count(session, context)

    if count is not None:
        limit = context.definition.items.max if context.definition.items else 0
        if count < limit:
            buttons.append(_button("add", "add", "➕", locale))
        if count > 1:
            buttons.append(_button("remove", "remove", "🗑️", locale))
    if step.preview:
        buttons.append(_button("preview", "aside:preview", "👁️", locale))
    return (*buttons, confirm_button(locale), cancel_button(locale))


def render(step: Any, session: FormSession, context: RenderContext) -> Screen:
    """The review card: every answer in blocks with their Edit, actions below."""
    locale = context.locale
    document = to_document(context.steps, session.answers, locale)
    rows = manager.panel_rows(context.definition, document, locale)
    title = title_of(step, locale)
    groups = manager.panel_groups(rows, title, locale)
    keyed = {group.key for group in groups if group.key}
    options = manager.edit_options(context.definition, document, locale)
    covered = bool(groups) and all(_has_edit(option.value, keyed) for option in options)
    panel = Panel(
        title=title,
        intro=description_of(step, session, context),
        groups=groups,
        thumbnail=KeikoIcons.IMAGE_03,
        edit_label=label("edit", locale),
    )
    return Screen(
        components=(panel,),
        buttons=_buttons(step, session, context, covered),
        flavour="components_v2",
        layout_footer=footer_of(step, locale),
    )


def parse(
    step: Any, payload: Any, session: FormSession, context: RenderContext
) -> Mapping[str, Answer]:
    """Confirming the review answers nothing; it commits."""
    return {}

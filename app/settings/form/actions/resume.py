"""The last screen: every answer listed, then Edit, Add, Remove, Confirm."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.constants import Emojis
from app.settings.form.actions.action import (
    RenderContext,
    cancel_button,
    caption,
    confirm_button,
    label,
    step_screen,
    views,
)
from app.settings.form.components import Button, Screen
from app.settings.form.copy import text
from app.settings.form.form_state import Answer, FormSession
from app.settings.form.responses.styles import empty_value, format_value
from app.settings.form.responses.summary import ResponseView


def composition_lines(
    title: str, items: Sequence[Mapping[str, Any]], locale: str
) -> str:
    """One numbered block per item, one line per visible field."""
    result = ""
    for number, item in enumerate(items, start=1):
        lines = ""
        for entry in item.values():
            if not isinstance(entry, Mapping) or entry.get("hidden"):
                continue
            formatted = format_value(entry.get("value"), entry.get("style"), locale)
            lines += f"- {entry['title']}: **{formatted or empty_value(locale)}**\n"
        result += f"\n{Emojis.FRISBEE_EMOJI} **{title} #{number}**\n{lines}"
    return result


def settings_summary(listed: Sequence[ResponseView], locale: str) -> str:
    """The `:pencil: Settings` block the review appends to its description."""
    heading = text("commands.resume.settings", locale)
    result = f"\n\n:pencil: **{heading}**\n"

    for view in listed:
        if view.hidden:
            continue
        if view.style == "composition":
            lines = composition_lines(view.title, view.value, locale)
            if lines:
                result += f"\n{lines}"
            continue
        formatted = format_value(view.value, view.style, locale)
        if isinstance(formatted, str) and "\n" in formatted:
            result += (
                f"\n{Emojis.FRISBEE_EMOJI} {view.title}:\n"
                f"**{formatted or empty_value(locale)}**"
            )
        else:
            result += (
                f"\n{Emojis.FRISBEE_EMOJI} {view.title}: "
                f"**{formatted or empty_value(locale)}**"
            )
    return result


def _item_count(session: FormSession, context: RenderContext) -> int | None:
    composition = context.definition.composition
    if composition is None:
        return None
    answer = session.answers.get(composition.key)
    return len(answer.raw or ()) if answer else 0


def render(step: Any, session: FormSession, context: RenderContext) -> Screen:
    """The review embed with the settings summary and its action buttons."""
    locale = context.locale
    buttons: list[Button] = [
        Button(
            label("edit", locale),
            "edit",
            "secondary",
            "📝",
            description=caption("edit", locale),
        )
    ]
    count = _item_count(session, context)

    if count is not None:
        limit = context.definition.items.max if context.definition.items else 0
        if count < limit:
            buttons.append(
                Button(
                    label("add", locale),
                    "add",
                    "secondary",
                    "➕",
                    description=caption("add", locale),
                )
            )
        if count > 1:
            buttons.append(
                Button(
                    label("remove", locale),
                    "remove",
                    "secondary",
                    "🗑️",
                    description=caption("remove", locale),
                )
            )
    if step.preview:
        buttons.append(
            Button(
                label("preview", locale),
                "aside:preview",
                "secondary",
                "👁️",
                description=caption("preview", locale),
            )
        )
    buttons += [confirm_button(locale), cancel_button(locale)]
    description = step.description.get(locale) + settings_summary(
        views(session, context), locale
    )
    return step_screen(
        step, session, context, buttons=tuple(buttons), description=description
    )


def parse(
    step: Any, payload: Any, session: FormSession, context: RenderContext
) -> Mapping[str, Answer]:
    """Confirming the review answers nothing; it commits."""
    return {}

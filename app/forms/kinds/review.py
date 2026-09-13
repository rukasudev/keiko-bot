"""The last screen: every answer listed, then Edit, Add, Remove, Confirm."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.constants import Emojis
from app.forms.engine.screen import Button, Screen
from app.forms.engine.session import Answer, FormSession
from app.forms.engine.summary import ResponseView
from app.forms.extensions.copy import text
from app.forms.extensions.formatters import format_value
from app.forms.kinds.common import (
    cancel_button,
    caption,
    confirm_button,
    label,
    step_screen,
    views,
)
from app.forms.kinds.context import RenderContext


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
            lines += f"- {entry['title']}: **{formatted or '-'}**\n"
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
            result += f"\n{composition_lines(view.title, view.value, locale)}"
            continue
        formatted = format_value(view.value, view.style, locale)
        if isinstance(formatted, str) and "\n" in formatted:
            result += f"\n{Emojis.FRISBEE_EMOJI} {view.title}:\n**{formatted or '-'}**"
        else:
            result += f"\n{Emojis.FRISBEE_EMOJI} {view.title}: **{formatted or '-'}**"
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

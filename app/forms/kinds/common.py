"""What every step screen shares: copy resolution, buttons, thumbnails."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.constants import KeikoIcons, Style
from app.forms.definitions.schema import Step
from app.forms.engine.rules import evaluate
from app.forms.engine.screen import Button, Component, Field, Screen
from app.forms.engine.session import FormSession
from app.forms.engine.summary import ResponseView, responses
from app.forms.extensions.copy import text
from app.forms.extensions.links import link_host
from app.forms.kinds.context import RenderContext

TOKEN = re.compile(r"\{response:([A-Za-z0-9_]+)(?::([a-z_]+))?(?:\|([^}]*))?\}")
TOKEN_FORMATTERS = {"host": link_host}

THUMBNAILS: dict[str, str] = {
    "review": KeikoIcons.IMAGE_03,
    "info": KeikoIcons.IMAGE_02,
}


def label(key: str, locale: str) -> str:
    """A button label from the language files."""
    return text(f"buttons.{key}.label", locale)


def caption(key: str, locale: str) -> str:
    """A button caption from the language files, for the help screen."""
    return text(f"buttons.{key}.desc", locale)


def confirm_button(locale: str) -> Button:
    """The green Confirm."""
    return Button(
        label("confirm", locale),
        "confirm",
        "success",
        description=caption("confirm", locale),
    )


def cancel_button(locale: str) -> Button:
    """The red Cancel, always last."""
    return Button(
        label("cancel", locale),
        "cancel",
        "danger",
        description=caption("cancel", locale),
    )


def back_button(locale: str) -> Button:
    """The grey Back."""
    return Button(
        label("back", locale), "back", "secondary", description=caption("back", locale)
    )


def step_buttons(
    locale: str, context: RenderContext, *extra: Button
) -> tuple[Button, ...]:
    """The step's own buttons, then Back when possible, then Cancel."""
    buttons: list[Button] = list(extra)
    if context.can_go_back:
        buttons.append(back_button(locale))
    buttons.append(cancel_button(locale))
    return tuple(buttons)


def views(session: FormSession, context: RenderContext) -> tuple[ResponseView, ...]:
    """The session's answers as the summary lists them."""
    return responses(context.steps, session.answers, context.locale)


def apply_tokens(body: str, session: FormSession, context: RenderContext) -> str:
    """`{response:key:formatter|fallback}` tokens replaced by earlier answers."""
    by_key = {view.key: view for view in views(session, context)}

    def replace(match: re.Match[str]) -> str:
        key, formatter, fallback = match.group(1), match.group(2), match.group(3) or ""
        view = by_key.get(key)
        value: Any = view.value if view else None
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        if value in (None, ""):
            return fallback
        if formatter in TOKEN_FORMATTERS:
            return TOKEN_FORMATTERS[formatter](str(value))
        return str(value)

    return TOKEN.sub(replace, body)


def description_of(step: Step, session: FormSession, context: RenderContext) -> str:
    """The step description, its active variant chosen and tokens resolved."""
    body = step.description.get(context.locale)
    for variant in step.description_when:
        if evaluate(variant.when, context.scope):
            body = variant.text.get(context.locale)
            break
    return apply_tokens(body, session, context)


def title_of(step: Step, locale: str) -> str:
    """The step title with its emoji in front."""
    title = step.title.get(locale)
    return f"{step.emoji} {title}" if step.emoji else title


def footer_of(step: Step, locale: str) -> str:
    """The step footer, empty when the step has none."""
    return step.footer.get(locale) if step.footer else ""


def step_screen(
    step: Step,
    session: FormSession,
    context: RenderContext,
    *,
    buttons: Sequence[Button],
    components: Sequence[Component] = (),
    fields: Sequence[Field] = (),
    description: str | None = None,
) -> Screen:
    """The embed screen of a step: title, description, footer, thumbnail."""
    return Screen(
        title=title_of(step, context.locale),
        description=(
            description_of(step, session, context)
            if description is None
            else description
        ),
        footer=footer_of(step, context.locale),
        fields=tuple(fields),
        components=tuple(components),
        buttons=tuple(buttons),
        flavour="embed",
        thumbnail=THUMBNAILS.get(step.kind, KeikoIcons.IMAGE_01),
        color=Style.BACKGROUND_COLOR,
    )

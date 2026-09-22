"""What every action shares: the context it renders in and its common parts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.constants import KeikoIcons, Style
from app.settings.form.components import (
    Button,
    Component,
    DesignCard,
    Field,
    Gallery,
    Screen,
)
from app.settings.form.conditions import Scope, evaluate
from app.settings.form.copy import text
from app.settings.form.form_state import EditItem, FormSession
from app.settings.form.form_yaml import Design, FormDefinition, Step
from app.settings.form.responses.links import link_host
from app.settings.form.responses.summary import ResponseView, responses


@dataclass(frozen=True)
class PanelRow:
    """One line of the manager panel, already localized."""

    key: str
    title: str
    value: Any
    style: str | None = None
    icon: str | None = None
    group: str | None = None
    group_title: str | None = None
    group_icon: str | None = None
    hidden: bool = False
    target: str | None = None
    per_item: bool = False
    group_declared: bool = False
    edit_label: str | None = None
    group_order: int = 0
    group_screen: bool = False


@dataclass(frozen=True)
class RenderContext:
    """The definition, the steps in play, the scope chain and prefetched data."""

    definition: FormDefinition
    steps: Sequence[Step]
    locale: str
    scope: Scope
    parent_values: Mapping[str, Any] = field(default_factory=dict)
    document: Mapping[str, Any] = field(default_factory=dict)
    items: Sequence[Mapping[str, Any]] = ()
    external: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    server_name: str = ""
    prefix: str = ""
    previews: Mapping[str, str] = field(default_factory=dict)
    panel_rows: Sequence[PanelRow] | None = None
    panel_info: str = ""
    panel_info_title: str = ""
    extra_buttons: Sequence[Button] = ()
    enabled: bool = True
    can_go_back: bool = False
    item_index: int | None = None


TOKEN = re.compile(r"\{response:([A-Za-z0-9_]+)(?::([a-z_]+))?(?:\|([^}]*))?\}")
CONTEXT_TOKEN = re.compile(r"\{context:([a-z_]+)\}")
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
    """`{response:...}` tokens from earlier answers, `{context:...}` from here."""
    by_key = {view.key: view for view in views(session, context)}
    around = {"server_name": context.server_name, "prefix": context.prefix}

    def replace(match: re.Match[str]) -> str:
        key, formatter, fallback = match.group(1), match.group(2), match.group(3) or ""
        view = by_key.get(key)
        value: Any = view.value if view else session.raw(key)

        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        if value in (None, ""):
            return fallback
        if formatter in TOKEN_FORMATTERS:
            return TOKEN_FORMATTERS[formatter](str(value))
        return str(value)

    def surroundings(match: re.Match[str]) -> str:
        return str(around.get(match.group(1), ""))

    return CONTEXT_TOKEN.sub(surroundings, TOKEN.sub(replace, body))


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


def other_items(
    session: FormSession, items: Sequence[Mapping[str, Any]]
) -> tuple[Mapping[str, Any], ...]:
    """The composition's items except the one this session edits."""
    mode = session.mode
    if isinstance(mode, EditItem):
        return tuple(item for index, item in enumerate(items) if index != mode.index)
    return tuple(items)


def design_gallery(
    step_key: str, designs: Sequence[Design], context: RenderContext
) -> Gallery:
    """A gallery of `designs` with their previews, header, footer and Select."""
    locale = context.locale
    return Gallery(
        step_key=step_key,
        header=text("buttons.components.design-select.header", locale),
        designs=tuple(
            DesignCard(
                key=design.key,
                label=design.label.get(locale),
                description=design.description.get(locale),
                preview_url=context.previews.get(design.key),
            )
            for design in designs
        ),
        footer=text("buttons.components.design-select.footer", locale),
        select_label=label("select", locale),
    )

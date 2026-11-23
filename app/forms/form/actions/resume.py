"""The last screen: a card of every answer, then Preview, Add, Remove, Confirm."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from app.constants import KeikoIcons
from app.core.conditions import Scope, evaluate
from app.forms.form.actions import Refusal
from app.forms.form.actions.action import (
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
from app.forms.form.actions.configuration_card import (
    hidden_keys,
    joined_labels,
    required_labels,
    state_of,
    unfilled,
)
from app.forms.form.components import Button, Panel, PanelGroup, Screen
from app.forms.form.form_state import Answer, FormSession
from app.forms.form.form_yaml import (
    CardStep,
    ContentStep,
    FormDefinition,
    ReviewStep,
    Step,
    TextStep,
    produced_keys,
)
from app.forms.form.responses.responses import to_document
from app.forms.form.responses.summary import (
    every_setting_has_a_button,
    kept_items,
    labelled,
    panel_groups,
    panel_rows,
    section_gates,
)


def _item_count(session: FormSession, context: Context) -> int | None:
    composition = context.definition.composition
    if composition is None or composition.appears_in == "panel":
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
        value = session.raw(line.answer)
        if value in (None, "", [], (), {}):
            continue
        lines.append(f"{line.emoji} {labelled(line.label.get(locale), str(value))}")
    return PanelGroup(None, "", tuple(lines)) if lines else None


def _with_media(
    groups: Sequence[PanelGroup], steps: Sequence[Step], session: FormSession
) -> tuple[PanelGroup, ...]:
    pictures = {
        step.key: str(session.raw(step.image) or "")
        for step in steps
        if isinstance(step, TextStep) and step.image
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
) -> Mapping[str, Answer] | Refusal:
    """Confirming the review answers nothing, once every key it requires is given."""
    review: ReviewStep = step
    if not review.required.named():
        return {}
    values, hidden, labels = _settings(session, context)
    missing, alternatives = unfilled(review.required, values, hidden)

    if alternatives:
        return Refusal("fill_one_field", plain=True, missing=alternatives)
    if missing:
        named = [labels.get(key, key) for key in missing]
        return Refusal(
            "required_fields",
            {"fields": joined_labels(named, context.locale)},
            plain=True,
            missing=missing,
        )
    return {}


def _settings(
    session: FormSession, context: Context
) -> tuple[dict[str, Any], set[str], dict[str, str]]:
    values = dict(session.values())
    hidden: set[str] = set()
    labels: dict[str, str] = {}

    for step in context.definition.steps:
        if not isinstance(step, ContentStep):
            continue
        keys = produced_keys(step)
        labels.update({key: step.title.get(context.locale) for key in keys})
        if isinstance(step, CardStep):
            state = state_of(step, session, context)
            values.update(state)
            hidden.update(hidden_keys(step, state, context))
            labels.update(required_labels(step, context.locale))
        if step.appears_in == "panel" or not evaluate(step.when, context.scope):
            hidden.update(keys)
    return values, hidden, labels


def kept(
    definition: FormDefinition, values: Mapping[str, Any], locale: str
) -> Refusal | None:
    """The refusal of a change to a saved feature that leaves the review's rule unmet,
    None while the rule still holds."""
    review = definition.steps[-1] if definition.steps else None
    if not isinstance(review, ReviewStep) or not review.required.named():
        return None
    scope = Scope(dict(values))
    gates = section_gates(definition.steps)
    hidden = {key for key, gate in gates.items() if not evaluate(gate, scope)}
    labels: dict[str, str] = {}

    for step in definition.steps:
        if not isinstance(step, ContentStep):
            continue
        keys = produced_keys(step)
        labels.update({key: step.title.get(locale) for key in keys})
        if isinstance(step, CardStep):
            labels.update(required_labels(step, locale))
        if step.appears_in == "setup" or not evaluate(step.when, scope):
            hidden.update(keys)
    missing, alternatives = unfilled(review.required, values, hidden)

    named = [labels.get(key, key) for key in alternatives or missing]
    if not named:
        return None
    fields = {"fields": joined_labels(named, locale)}
    if alternatives:
        return Refusal("commands.responses.keep_one_field", fields, warning=True)
    return Refusal("required_fields", fields, plain=True)

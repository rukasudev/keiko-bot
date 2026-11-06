"""A Components V2 card: sections that each edit part of one answer.

The card's draft lives in the session as the parts of the card's own key;
every section change is a `Drafted` event, Done turns the draft into the
answers the card's fields declare. Each section `type` the YAML may declare
is one `SectionKind` in `SECTION_KINDS`, the way each step kind is one `Kind`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.constants import KeikoIcons
from app.settings.form.actions import Refusal
from app.settings.form.actions.action import (
    Context,
    apply_tokens,
    back_button,
    cancel_button,
    design_gallery,
    footer_of,
    other_items,
)
from app.settings.form.actions.selects import picked_ids, select_picker, stored_ids
from app.settings.form.components import (
    Button,
    Card,
    CardHeader,
    Choice,
    ChoiceOption,
    FileInput,
    Input,
    OptionSelect,
    Picker,
    Screen,
    SectionView,
    TextInputs,
)
from app.settings.form.conditions import Scope, evaluate
from app.settings.form.copy import text
from app.settings.form.form_state import Answer, FormSession, Refused
from app.settings.form.form_yaml import (
    BooleanToggleSection,
    ButtonOptionsSection,
    CardStep,
    ChannelSelectSection,
    CompositionStep,
    DesignSection,
    FileUploadSection,
    ModalInputSection,
    MultiSelectSection,
    Option,
    Section,
    SelectsSection,
    Text,
    TitleContentSection,
    UserSelectSection,
    ValueSelectSection,
    owned_keys,
)
from app.settings.form.responses.responses import document_values
from app.settings.form.responses.styles import empty_value, format_boolean, format_value
from app.settings.form.responses.summary import (
    item_entries,
    item_name,
    list_adder,
    list_bullets,
    list_remover,
    option_label,
    step_title,
)
from app.settings.form.responses.transforms import normalizer, transform
from app.settings.form.responses.validations import ValidationContext, validator

ICONS: dict[str, str] = {
    "IMAGE_01": KeikoIcons.IMAGE_01,
    "IMAGE_02": KeikoIcons.IMAGE_02,
    "IMAGE_03": KeikoIcons.IMAGE_03,
    "BIRTHDAY_GIF": KeikoIcons.BIRTHDAY_GIF,
}
EMPTY: tuple[Any, ...] = (None, "", [])

Lines = tuple[tuple[str, ...], str | None]
Changes = Mapping[str, Any]
Preview = Callable[[Any, Mapping[str, Any], CardStep, FormSession, Context], Lines]
Press = Callable[
    [Any, CardStep, int, Mapping[str, Any], Context],
    tuple[Screen | None, Changes | None],
]
Change = Callable[
    [Any, Any, Mapping[str, Any], FormSession, Context], "Changes | Refusal"
]
Reset = Callable[[Any], Changes]
Custom = Callable[[Any, Mapping[str, Any]], bool]
Listed = tuple["CompositionStep | None", list[dict[str, Any]]]
Control = Picker | OptionSelect | Choice
Controls = Callable[[Any, CardStep, Mapping[str, Any], Context], tuple[Control, ...]]


def _localized(value: Any, locale: str) -> Any:
    return value.get(locale) if isinstance(value, Text) else value


def _seed_state(
    card: CardStep, session: FormSession, context: Context
) -> dict[str, Any]:
    keys = card.state_keys()
    saved = document_values(context.document)
    state: dict[str, Any] = {}

    for key in keys:
        value = session.raw(key)
        if isinstance(value, Mapping):
            value = value.get("value")
        if value is None and key in saved:
            value = saved.get(key)
        state[key] = value
    rule = transform(card.transform)
    if rule:
        stored = session.raw(rule.value_key)
        if stored is None:
            stored = saved.get(rule.value_key)
        for part, part_value in rule.hydrate(stored).items():
            if part in keys and not state.get(part):
                state[part] = part_value
    return state


def initial_state(
    card: CardStep, session: FormSession, context: Context
) -> dict[str, Any]:
    """The card state before any change: prior answers, the document, defaults."""
    state = _seed_state(card, session, context)
    for key, value in card.defaults.items():
        if state.get(key) is None:
            state[key] = _localized(value, context.locale)
    for section in card.sections:
        for select in section.picks():
            if state.get(select.key) is None:
                state[select.key] = stored_ids(())
        mode = section.state.mode
        if not mode or state.get(mode) in ("default", "custom"):
            continue
        payload = [
            key
            for key in (section.state.title, section.state.content, section.state.url)
            if key
        ]
        state[mode] = "custom" if any(state.get(key) for key in payload) else "default"
    return state


def state_of(card: CardStep, session: FormSession, context: Context) -> dict[str, Any]:
    """The current draft, or the initial state when nothing was changed yet."""
    draft = session.answers.get(card.key)
    if draft is not None and draft.parts:
        return dict(draft.parts)
    return initial_state(card, session, context)


def visible(section: Section, state: Mapping[str, Any], context: Context) -> bool:
    """True unless the section's rule hides it, or it belongs to a setup only."""
    if section.setup_only and context.document:
        return False
    return evaluate(section.visible_when, Scope(state))


def held_keys(card: CardStep, state: Mapping[str, Any], context: Context) -> set[str]:
    """The sections a visible group draws on its own screen, never on the card."""
    return {
        key
        for group in card.sections
        if group.holds() and visible(group, state, context)
        for key in group.holds()
    }


def opened_group(
    card: CardStep, state: Mapping[str, Any], session: FormSession, context: Context
) -> Section | None:
    """The visible group whose screen the session shows, None on the card."""
    return next(
        (
            section
            for section in card.sections
            if section.key == session.opened_group
            and section.holds()
            and visible(section, state, context)
        ),
        None,
    )


def hidden_keys(card: CardStep, state: Mapping[str, Any], context: Context) -> set[str]:
    """State keys owned only by sections hidden for this state."""
    keys: set[str] = set()
    for section in card.sections:
        if not visible(section, state, context):
            keys.update(owned_keys(section))
    return keys


def is_custom(section: Section, state: Mapping[str, Any]) -> bool:
    """True when a default-or-custom section holds a custom value."""
    custom = SECTION_KINDS[section.type].custom
    return custom(section, state) if custom is not None else True


def preview(
    section: Section,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    """The lines under a section heading, and a media url when it shows one."""
    return SECTION_KINDS[section.type].preview(section, state, card, session, context)


def _formatted(value: Any, style: str | None, locale: str) -> str:
    if value in EMPTY:
        return empty_value(locale)
    return format_value(value, style, locale) if style else str(value)


def _section_view(
    index: int,
    section: Section,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> SectionView:
    locale = context.locale
    label = section.label.get(locale)
    customize = section.customize_label.get(locale) if section.customize_label else ""
    lines, media = preview(section, state, card, session, context)
    custom = SECTION_KINDS[section.type].custom
    described = section.description.get(locale) if section.description else ""
    description = apply_tokens(described, session, context, state).rstrip()

    if section.media_key and not media:
        found = state.get(section.media_key) or session.raw(section.media_key)
        media = str(found) if found else None
    if custom is not None and section.state.mode:
        customized = custom(section, state)
        badge_key = "badge-custom" if customized else "badge-default"
        badge = text(f"buttons.summary-card.{badge_key}", locale)
        heading = f"{section.icon} **{label}:** {badge}"
        buttons: tuple[Button, ...]

        if customized:
            buttons = (
                Button(text("buttons.summary-card.edit", locale), f"section:{index}"),
                Button(text("buttons.summary-card.reset", locale), f"reset:{index}"),
            )
        else:
            buttons = (Button(customize, f"section:{index}"),)
        return SectionView(
            index, section.key, heading, lines, buttons, media, description
        )
    if section.adds_to():
        return _list_view(index, section, state, session, context, lines, description)
    edit_label = customize or text("buttons.summary-card.edit", locale)
    return SectionView(
        index,
        section.key,
        f"{section.icon} **{label}**",
        lines,
        (Button(edit_label, f"section:{index}"),),
        media,
        description,
    )


def _listed(section: Section, session: FormSession, context: Context) -> Listed:
    composition = next(
        (
            step
            for step in context.definition.steps
            if isinstance(step, CompositionStep) and step.key == section.adds_to()
        ),
        None,
    )
    if composition is None:
        return None, []
    answer = session.answers.get(composition.key)
    items = [
        item_entries(composition.steps, item, context.locale)
        for item in (answer.raw or () if answer else ())
    ]
    return composition, items


def _list_view(
    index: int,
    section: Section,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
    lines: tuple[str, ...],
    description: str,
) -> SectionView:
    locale = context.locale
    composition, items = _listed(section, session, context)
    assert composition is not None
    title = step_title(composition, locale, Scope(state))
    add = list_adder(composition, len(items), f"section:{index}", locale)
    buttons = (add, *list_remover(composition, len(items), locale))
    heading = f"{section.icon} **{title}**"
    return SectionView(index, section.key, heading, lines, buttons, None, description)


def _list_preview(
    section: Section,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    locale = context.locale
    composition, items = _listed(section, session, context)
    if composition is None or not items:
        return ((f"> {empty_value(locale)}",), None)
    return (list_bullets(composition, items, locale), None)


def _opened_by_the_form(
    section: Section,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    return None, {}


def _unchanged(
    section: Section,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    return {}


def _group_preview(
    section: Section,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    lines = [
        line
        for held in card.sections
        if held.key in section.holds() and visible(held, state, context)
        for line in _held_lines(held, state, card, session, context)
    ]
    return (tuple(lines), None)


def _held_lines(
    section: Section,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> tuple[str, ...]:
    locale = context.locale
    if section.picks():
        return tuple(
            f"> {select.icon or section.icon} **{select.label.get(locale)}:** "
            f"{_formatted(state.get(select.key), select.style, locale)}"
            for select in section.picks()
        )
    if section.adds_to():
        composition, items = _listed(section, session, context)
        assert composition is not None
        names = ", ".join(
            item_name(composition, item, index, locale, styled=True)
            for index, item in enumerate(items)
        )
        title = step_title(composition, locale, Scope(state))
        return (f"> {section.icon} **{title}:** {names or empty_value(locale)}",)
    lines, _ = preview(section, state, card, session, context)
    value = " ".join(line.removeprefix("> ") for line in lines)
    return (f"> {section.icon} **{section.label.get(locale)}:** {value}",)


def _header(card: CardStep, session: FormSession, context: Context) -> CardHeader:
    locale = context.locale
    header = card.header

    if header is None:
        return CardHeader(card.title.get(locale), card.description.get(locale))
    title = f"{header.title_emoji} {header.title.get(locale)}".strip()
    lines = []

    for line in header.lines:
        value = session.raw(line.value_key)
        shown = (
            format_value(value, line.value_format, locale)
            if value is not None and line.value_format
            else ("" if value is None else str(value))
        )
        lines.append(f"{line.emoji} **{line.label.get(locale)}:** {shown}")
    return CardHeader(
        title=title,
        description=card.description.get(locale),
        lines=tuple(lines),
        thumbnail=ICONS.get(header.thumbnail, ""),
    )


def _names_the_item(section: Section, context: Context) -> bool:
    composition = context.definition.composition
    if composition is None or not composition.items.unique_locked:
        return False
    unique = composition.items.unique_by
    return context.item_index is not None and unique in owned_keys(section)


def _with_controls(
    view: SectionView,
    section: Section,
    card: CardStep,
    state: Mapping[str, Any],
    context: Context,
) -> SectionView:
    if _names_the_item(section, context):
        value = _formatted(
            state.get(section.state.value or ""), section.style, context.locale
        )
        shown = view.preview or (f"> {value}",)
        return replace(view, preview=shown, buttons=(), controls=())
    controls = SECTION_KINDS[section.type].controls
    if controls is None:
        return view
    return replace(view, buttons=(), controls=controls(section, card, state, context))


def _drawn(
    card: CardStep,
    keys: Sequence[str],
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> tuple[SectionView, ...]:
    return tuple(
        _with_controls(
            _section_view(index, section, state, card, session, context),
            section,
            card,
            state,
            context,
        )
        for index, section in enumerate(card.sections)
        if section.key in keys and visible(section, state, context)
    )


def _group_screen(
    card: CardStep,
    group: Section,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Screen:
    locale = context.locale
    described = group.picker_description or group.description
    description = described.get(locale) if described else ""
    header = CardHeader(
        _picker_title(group, locale),
        apply_tokens(description, session, context, state).rstrip(),
    )
    sections = _drawn(card, group.holds(), state, session, context)
    return Screen(
        components=(Card(card.key, header, sections),),
        buttons=(Button(text("buttons.back.label", locale), "picker_back"),),
        flavour="components_v2",
        layout_footer=footer_of(card, locale),
    )


def render(step: Any, session: FormSession, context: Context) -> Screen:
    """The card with its visible sections and buttons, or the group it opened."""
    card: CardStep = step
    locale = context.locale
    state = state_of(card, session, context)
    group = opened_group(card, state, session, context)

    if group is not None:
        return _group_screen(card, group, state, session, context)
    held = held_keys(card, state, context)
    keys = [section.key for section in card.sections if section.key not in held]
    sections = _drawn(card, keys, state, session, context)
    buttons: list[Button] = []

    if card.preview:
        buttons.append(
            Button(
                text("buttons.preview.label", locale),
                "aside:preview",
                "secondary",
                "👁️",
                description=text("buttons.preview.desc", locale),
            )
        )
    done = "confirm.label" if context.done_saves else "summary-card.done"
    buttons.append(Button(text(f"buttons.{done}", locale), "done", "success"))
    if context.can_go_back:
        buttons.append(back_button(locale))
    buttons.append(cancel_button(locale))
    return Screen(
        components=(Card(card.key, _header(card, session, context), sections),),
        buttons=tuple(buttons),
        flavour="components_v2",
        layout_footer=footer_of(card, locale),
    )


def open_section(
    card: CardStep, index: int, session: FormSession, context: Context
) -> tuple[Screen | None, Changes | None]:
    """What pressing a section does: a screen to show, or a change to apply."""
    section = card.sections[index]
    state = state_of(card, session, context)
    screen, changes = SECTION_KINDS[section.type].press(
        section, card, index, state, context
    )
    refused = session.refused_on(f"section:{index}")

    if screen is not None and refused is not None and refused.inputs:
        screen = _typed_again(screen, refused)
    return screen, changes


def _typed_again(screen: Screen, refused: Refused) -> Screen:
    components = tuple(
        replace(
            component,
            inputs=tuple(
                field if typed is None else replace(field, default=typed)
                for field, typed in zip(
                    component.inputs, refused.typed_into(len(component.inputs))
                )
            ),
        )
        if isinstance(component, TextInputs)
        else component
        for component in screen.components
    )
    return replace(screen, components=components)


def apply_change(
    card: CardStep,
    index: int,
    payload: Any,
    session: FormSession,
    context: Context,
) -> Changes | Refusal:
    """The state changes a section's payload produces, or a refusal."""
    section = card.sections[index]
    state = state_of(card, session, context)

    if _names_the_item(section, context):
        return {}
    change = SECTION_KINDS[section.type].change(
        section, payload, state, session, context
    )

    if isinstance(change, Refusal) and not change.target:
        return replace(change, target=f"section:{index}")
    return change


def apply_drafts(
    card: CardStep,
    changes: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes | Refusal:
    """What a section's select reported, through the section's own rules."""
    applied: dict[str, Any] = {}
    for slot, payload in changes.items():
        index = next(
            (
                index
                for index, section in enumerate(card.sections)
                if slot in owned_keys(section)
            ),
            None,
        )
        if index is None:
            applied[slot] = payload
            continue
        own = card.sections[index].state.value == slot
        drafted = payload if own else {slot: payload}
        change = apply_change(card, index, drafted, session, context)
        if isinstance(change, Refusal):
            return change
        applied.update(change)
    return applied


def reset_section(card: CardStep, index: int) -> Changes:
    """The state a section goes back to on Reset."""
    section = card.sections[index]
    return SECTION_KINDS[section.type].reset(section)


def draft(
    card: CardStep,
    changes: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Answer:
    """The card draft with `changes` applied."""
    state = state_of(card, session, context)
    state.update(changes)
    return Answer(None, state)


def required_labels(card: CardStep, locale: str) -> dict[str, str]:
    """State key to the label named when it is missing."""
    labels: dict[str, str] = {}
    for field in card.fields:
        labels[field.key] = field.label.get(locale)
    for section in card.sections:
        for key in owned_keys(section):
            labels[key] = section.label.get(locale)
    return labels


def _join_labels(labels: Sequence[str], locale: str) -> str:
    emphasized = [f"**{label}**" for label in labels]
    if len(emphasized) <= 1:
        return emphasized[0] if emphasized else ""
    conjunction = text("buttons.summary-card.required-separator", locale)
    if len(emphasized) == 2:
        return conjunction.join(emphasized)
    return f"{', '.join(emphasized[:-1])}{conjunction}{emphasized[-1]}"


def parse(
    step: Any, payload: Any, session: FormSession, context: Context
) -> Mapping[str, Answer] | Refusal:
    """Done: the draft becomes the answers the card's fields declare."""
    card: CardStep = step
    state = dict(state_of(card, session, context))
    hidden = hidden_keys(card, state, context)
    missing = [
        key
        for key in card.required_keys
        if key not in hidden and state.get(key) in EMPTY
    ]
    alternatives = [key for key in card.required_any if key not in hidden]

    if alternatives and all(state.get(key) in EMPTY for key in alternatives):
        return Refusal(
            "fill-one-field", plain=True, target=card.key, missing=tuple(alternatives)
        )
    if missing:
        labels = required_labels(card, context.locale)
        joined = _join_labels([labels.get(key, key) for key in missing], context.locale)
        return Refusal(
            "buttons.summary-card.required",
            {"fields": joined},
            plain=True,
            target=card.key,
            missing=tuple(missing),
        )
    rule = transform(card.transform)
    if rule:
        state[rule.value_key] = rule.serialize(state)
    answers: dict[str, Answer] = {card.key: Answer(None, state)}
    for field in card.fields:
        if field.key in state:
            answers[field.key] = Answer(state[field.key])
    return answers


def preview_refusal(
    card: CardStep, session: FormSession, context: Context
) -> Refusal | None:
    """The declared error while a key the card's preview needs is empty."""
    needs = card.preview_needs
    if needs is None:
        return None
    state = state_of(card, session, context)
    hidden = hidden_keys(card, state, context)
    missing = tuple(
        key for key in needs.keys if key not in hidden and state.get(key) in EMPTY
    )
    if not missing:
        return None
    return Refusal(needs.error, plain=True)


def _cleared(section: Section) -> Changes:
    return {section.state.value or "": None}


def _value_preview(
    section: Section,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    value = state.get(section.state.value or "")
    return ((f"> {_formatted(value, section.style, context.locale)}",), None)


@dataclass(frozen=True)
class SectionKind:
    """What a section type contributes: lines, press, change, reset, card controls."""

    press: Press
    change: Change
    preview: Preview = _value_preview
    reset: Reset = _cleared
    custom: Custom | None = None
    controls: Controls | None = None


def _picker_title(section: Section, locale: str) -> str:
    title = (
        section.picker_title.get(locale)
        if section.picker_title
        else section.label.get(locale)
    )
    if not section.icon or title.lstrip().startswith(section.icon):
        return title
    return f"{section.icon} {title}".strip()


def _picker_screen(
    card: CardStep,
    section: Section,
    locale: str,
    components: Sequence[Any],
    description: str = "",
    buttons: Sequence[Button] = (),
) -> Screen:
    return Screen(
        title=_picker_title(section, locale),
        description=description,
        components=tuple(components),
        buttons=(*buttons, Button(text("buttons.back.label", locale), "picker_back")),
        flavour="components_v2",
        layout_footer=footer_of(card, locale),
    )


def _options(
    options: Sequence[Option], selected: Sequence[str], locale: str
) -> tuple[ChoiceOption, ...]:
    return tuple(
        ChoiceOption(
            option.label.get(locale),
            str(option.value),
            option.style or "secondary",
            str(option.value) in selected,
            option.description.get(locale) if option.description else "",
        )
        for option in options
    )


def _modal_title(section: Section, locale: str) -> str:
    modal = section.opened_modal()
    title = modal.title.get(locale) if modal else section.label.get(locale)
    if not section.icon or title.lstrip().startswith(section.icon):
        return title
    return f"{section.icon} {title}".strip()


def _title_content_preview(
    section: TitleContentSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    locale = context.locale
    if is_custom(section, state):
        title = state.get(section.state.title or "") or ""
        content = state.get(section.state.content or "") or ""
    else:
        title = section.default.title.get(locale)
        content = section.default.content.get(locale)
    variables = _template_vars(card, session, context, state)
    lines = []

    if title:
        lines.append(f"> **{_fill(title, variables)}**")
    if content:
        lines.append(f"> {_fill(content, variables)}")
    return (tuple(lines), None)


def _template_vars(
    card: CardStep,
    session: FormSession,
    context: Context,
    state: Mapping[str, Any],
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for name, var in card.context.items():
        if var.context == "server_name":
            resolved[name] = context.server_name
            continue
        key = var.answer or ""
        value = state.get(key) if key in state else session.raw(key)
        if value is None:
            resolved[name] = ""
        elif var.format:
            resolved[name] = str(format_value(value, var.format, context.locale))
        else:
            resolved[name] = str(value)
    return resolved


def _fill(body: str, variables: Mapping[str, str]) -> str:
    for name, value in variables.items():
        body = body.replace("{" + name + "}", value)
    return body


def _title_content_modal(
    section: TitleContentSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    locale = context.locale
    modal = section.modal
    title_key, content_key = section.state.title or "", section.state.content or ""
    pair = (
        Input(
            _localized(modal.title_label, locale) or "",
            "title",
            default=state.get(title_key) or section.default.title.get(locale),
            max_length=50,
        ),
        Input(
            _localized(modal.content_label, locale) or "",
            "content",
            default=state.get(content_key) or section.default.content.get(locale),
            max_length=200,
            multiline=True,
        ),
    )

    return Screen(
        modal_title=_modal_title(section, locale),
        components=(TextInputs(card.key, pair, slot=section.key),),
        flavour="modal",
    ), None


def _title_content_change(
    section: TitleContentSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    values = payload.get("inputs") if isinstance(payload, Mapping) else payload
    title, content = (list(values or []) + ["", ""])[:2]
    return {
        section.state.mode or "": "custom",
        section.state.title or "": title,
        section.state.content or "": content,
    }


def _title_content_reset(section: TitleContentSection) -> Changes:
    return {
        section.state.mode or "": "default",
        section.state.title or "": None,
        section.state.content or "": None,
    }


def _title_content_custom(
    section: TitleContentSection, state: Mapping[str, Any]
) -> bool:
    return state.get(section.state.mode or "") == "custom"


def _image_preview(
    section: FileUploadSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    url = state.get(section.state.url or "") if is_custom(section, state) else None
    return ((), str(url) if url else None)


def _image_modal(
    section: FileUploadSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    locale = context.locale
    component = FileInput(
        card.key,
        text("buttons.file-upload.label", locale) or "Image",
        slot=section.key,
    )
    return Screen(
        modal_title=_modal_title(section, locale),
        components=(component,),
        flavour="modal",
    ), None


def _image_change(
    section: FileUploadSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    url = payload.get("url") if isinstance(payload, Mapping) else payload
    if not url:
        return {}
    uploaded: dict[str, Any] = {section.state.url or "": url}

    if section.state.mode:
        uploaded[section.state.mode] = "custom"
    return uploaded


def _image_reset(section: FileUploadSection) -> Changes:
    cleared: dict[str, Any] = {section.state.url or "": None}
    if section.state.mode:
        cleared[section.state.mode] = "default"
    return cleared


def _image_custom(section: FileUploadSection, state: Mapping[str, Any]) -> bool:
    mode, url = section.state.mode, section.state.url or ""
    if not mode:
        return bool(state.get(url))
    return state.get(mode) == "custom" and bool(state.get(url))


def _channel_picker(
    section: ChannelSelectSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    picker = _channel_select(section, card, context.locale)
    return _picker_screen(card, section, context.locale, (picker,)), None


def _channel_controls(
    section: ChannelSelectSection,
    card: CardStep,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Control, ...]:
    selected = picked_ids(state.get(section.state.value or ""))
    return (_channel_select(section, card, context.locale, selected),)


def _channel_select(
    section: ChannelSelectSection,
    card: CardStep,
    locale: str,
    selected: tuple[str, ...] = (),
) -> Picker:
    return Picker(
        card.key,
        "channel",
        text("buttons.components.select.channel-placeholder", locale),
        selected=selected,
        unique=True,
        slot=section.state.value or "",
        required=True,
    )


def _member_picker(
    section: UserSelectSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    picker = _member_select(section, card, context.locale)
    return _picker_screen(card, section, context.locale, (picker,)), None


def _member_controls(
    section: UserSelectSection,
    card: CardStep,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Control, ...]:
    selected = picked_ids(state.get(section.state.value or ""))
    return (_member_select(section, card, context.locale, selected),)


def _member_select(
    section: UserSelectSection,
    card: CardStep,
    locale: str,
    selected: tuple[str, ...] = (),
) -> Picker:
    return Picker(
        card.key,
        "user",
        text("buttons.components.select.user-placeholder", locale),
        selected=selected,
        unique=True,
        slot=section.state.value or "",
        required=True,
    )


def _member_preview(
    section: UserSelectSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    member = state.get(section.state.value or "")
    if not section.picture or member in EMPTY:
        return ((), None)
    return ((), context.member_pictures.get(str(member)) or None)


def _placeholder(section: Section, locale: str) -> str:
    if section.customize_label:
        return section.customize_label.get(locale)
    return _picker_title(section, locale)


def _options_select(
    section: ValueSelectSection | ButtonOptionsSection | DesignSection,
    card: CardStep,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Control, ...]:
    locale = context.locale
    key = section.state.value or ""
    current = [str(state.get(key))] if state.get(key) not in EMPTY else []
    select = OptionSelect(
        card.key,
        _placeholder(section, locale),
        _options(section.choices(), current, locale),
        slot=key,
    )
    return (select,)


def _picked(
    section: Section,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    value = payload[0] if isinstance(payload, (list, tuple)) else payload
    return {section.state.value or "": str(value)}


def _option_preview(
    section: ValueSelectSection | ButtonOptionsSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    locale = context.locale
    value = state.get(section.state.value or "")
    label = option_label(section.options, value, locale)
    return ((f"> {label or _formatted(value, section.style, locale)}",), None)


def _value_picker(
    section: ValueSelectSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    locale = context.locale
    single = OptionSelect(
        card.key,
        _picker_title(section, locale),
        _options(section.options, (), locale),
        1,
        1,
        slot=section.state.value or "",
    )
    description = _localized(section.picker_description, locale) or ""
    return _picker_screen(card, section, locale, (single,), description), None


def _value_change(
    section: ValueSelectSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    key = section.state.value or ""
    value = payload[0] if isinstance(payload, (list, tuple)) else payload
    changes: dict[str, Any] = {key: value}

    if str(state.get(key)) != str(value):
        _reset_dependents(section, state, value, changes)
    return changes


def _reset_dependents(
    section: ValueSelectSection,
    state: Mapping[str, Any],
    value: Any,
    changes: dict[str, Any],
) -> None:
    candidate = {**state, section.state.value or "": value}
    for rule in section.reset_on_change:
        check = validator(rule.validation)
        if check is None or not candidate.get(rule.key):
            continue
        if check.check(candidate.get(rule.key), ValidationContext(answers=candidate)):
            changes[rule.key] = None


def _option_buttons(
    section: ButtonOptionsSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    locale = context.locale
    chosen = str(state.get(section.state.value or ""))
    described = all(option.description for option in section.options)
    choice = Choice(
        card.key,
        tuple(
            ChoiceOption(
                option.label.get(locale),
                str(position),
                option.style or "secondary",
                str(option.value) == chosen,
                option.description.get(locale) if option.description else "",
            )
            for position, option in enumerate(section.options)
        ),
        button_label=text("buttons.choose.label", locale) if described else "",
    )
    description = _localized(section.picker_description, locale) or ""
    return _picker_screen(card, section, locale, (choice,), description), None


def _option_pressed(
    section: ButtonOptionsSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    key = section.state.value or ""
    if isinstance(payload, (list, tuple)):
        return {key: str(payload[0])} if payload else {}
    position = int(payload) if str(payload).isdigit() else -1
    if 0 <= position < len(section.options):
        return {key: str(section.options[position].value)}
    return {key: str(payload)}


def _values_preview(
    section: MultiSelectSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    value = state.get(section.state.value or "")
    chosen = [
        str(value)
        for value in (
            value if isinstance(value, (list, tuple)) else [value] if value else []
        )
    ]
    labels = [
        option.label.get(context.locale)
        for option in section.options
        if str(option.value) in chosen
    ]
    shown = ", ".join(labels) if labels else empty_value(context.locale)
    return ((f"> {shown}",), None)


def _values_picker(
    section: MultiSelectSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    locale = context.locale
    key = section.state.value or ""
    chosen = [str(value) for value in (state.get(key) or [])]
    multiple = OptionSelect(
        card.key,
        _picker_title(section, locale),
        _options(section.options, chosen, locale),
        0,
        max(len(section.options), 1),
        slot=key,
    )
    description = _localized(section.picker_description, locale) or ""
    return _picker_screen(card, section, locale, (multiple,), description), None


def _values_select(
    section: MultiSelectSection,
    card: CardStep,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Control, ...]:
    locale = context.locale
    key = section.state.value or ""
    chosen = [str(value) for value in (state.get(key) or [])]
    multiple = OptionSelect(
        card.key,
        _placeholder(section, locale),
        _options(section.options, chosen, locale),
        0,
        max(len(section.options), 1),
        slot=key,
    )
    return (multiple,)


def _values_change(
    section: MultiSelectSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    chosen = payload if isinstance(payload, (list, tuple)) else [payload]
    return {section.state.value or "": [str(value) for value in chosen]}


def _boolean_preview(
    section: BooleanToggleSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    value = state.get(section.state.value or "")
    return ((f"> {format_boolean(bool(value), context.locale)}",), None)


def _toggle(
    section: BooleanToggleSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    key = section.state.value or ""
    return None, {key: not bool(state.get(key))}


def _toggled(
    section: BooleanToggleSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    key = section.state.value or ""
    chosen = str(payload).lower()

    if chosen in ("true", "false"):
        return {key: chosen == "true"}
    return {key: not bool(state.get(key))}


def _yes_and_no(
    section: BooleanToggleSection,
    card: CardStep,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Control, ...]:
    key = section.state.value or ""
    current = bool(state.get(key))
    options = tuple(
        ChoiceOption(
            format_boolean(value, context.locale),
            str(value).lower(),
            "primary" if value == current else "secondary",
            value == current,
        )
        for value in (True, False)
    )
    return (Choice(card.key, options, slot=key),)


def _switched_off(section: BooleanToggleSection) -> Changes:
    return {section.state.value or "": False}


def _fields_preview(
    section: ModalInputSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    if len(section.modal.fields) <= 1:
        return _value_preview(section, state, card, session, context)
    lines = [
        f"> **{field.label.get(context.locale)}:** {state.get(field.key)}"
        for field in section.modal.fields
        if field.key
        and field.key != section.state.value
        and state.get(field.key) not in EMPTY
    ]
    value = state.get(section.state.value or "")
    lines.append(_formatted(value, section.style, context.locale).strip())
    return (tuple(lines), None)


def _fields_modal(
    section: ModalInputSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    locale = context.locale
    key = section.state.value or ""
    unkeyed = _parts(state.get(key))
    position = 0
    inputs: list[Input] = []

    for field in section.modal.fields:
        if field.key:
            saved = state.get(field.key)
        else:
            saved = unkeyed[position] if position < len(unkeyed) else None
            position += 1
        declared = field.default.get(locale) if field.default else None
        default = str(saved) if saved not in EMPTY else declared
        placeholder = field.placeholder.get(locale) if field.placeholder else None
        inputs.append(
            Input(
                field.label.get(locale),
                field.key,
                placeholder or default,
                default,
                field.required,
                field.max_length or 40,
                field.multiline,
            )
        )
    return Screen(
        modal_title=_modal_title(section, locale),
        components=(TextInputs(card.key, tuple(inputs), slot=section.key),),
        flavour="modal",
    ), None


def _parts(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(part) for part in value]
    if isinstance(value, str) and value:
        return [part.strip() for part in value.split(";")]
    return []


def _fields_change(
    section: ModalInputSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes | Refusal:
    key = section.state.value or ""
    values = payload.get("inputs") if isinstance(payload, Mapping) else [payload]
    typed = list(values or [""])
    fields = section.modal.fields
    changes: dict[str, Any] = {} if fields else {key: str(typed[0])}
    joined: list[str] = []

    for position, field in enumerate(fields):
        raw = str(typed[position]) if position < len(typed) else ""
        normalize = normalizer(field.normalize)
        value = normalize(raw) if normalize else raw

        if field.key:
            changes[field.key] = value
        elif value:
            joined.append(value)
    if any(field.key is None for field in fields):
        changes[key] = ";".join(joined) or None
    check = validator(section.modal.validation)

    if check:
        error = check.check(
            changes.get(key) or "",
            ValidationContext(
                answers=state,
                items=other_items(session, context.items),
                external=context.external,
            ),
        )
        if error:
            return Refusal(error, inputs=tuple(str(value) for value in typed))
    for answer, path in section.lookup_answers.items():
        service, _, name = path.partition(".")
        found = context.external.get(service, {})
        if name in found:
            changes[answer] = found[name]
    return {**changes, **_filled(section, state, changes)}


def _filled(
    section: ModalInputSection, state: Mapping[str, Any], changes: Changes
) -> Changes:
    fills = section.fills
    key = section.state.value or ""
    new = str(changes.get(key) or "")

    if fills is None or not new:
        return {}
    target = changes.get(fills.into, state.get(fills.into))
    if target in EMPTY:
        return {}
    old = str(state.get(key) or "")
    return {fills.into: _rewritten(target, fills.tokens, old, new)}


def _rewritten(body: Any, tokens: Mapping[str, str], old: str, new: str) -> Any:
    if isinstance(body, (list, tuple)):
        return [_rewritten(part, tokens, old, new) for part in body]
    written = str(body)

    if old and old.lower() != new.lower():
        renderings = {
            template.replace("{value}", old).lower(): template.replace("{value}", new)
            for template in tokens.values()
        }
        longest = sorted(renderings, key=len, reverse=True)
        bounded = re.compile(
            r"(?<![\w.])(?:" + "|".join(map(re.escape, longest)) + r")(?!\w)",
            flags=re.IGNORECASE,
        )
        written = bounded.sub(lambda match: renderings[match.group(0).lower()], written)
    for name, template in tokens.items():
        written = written.replace("{" + name + "}", template.replace("{value}", new))
    return written


def _selects_preview(
    section: SelectsSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    locale = context.locale
    shown = {
        select.key: _formatted(state.get(select.key), select.style, locale)
        for select in section.selects
    }

    if len(section.selects) == 1:
        return ((f"> {shown[section.selects[0].key]}",), None)
    lines = tuple(
        f"> {select.icon or section.icon} **{select.label.get(locale)}:** "
        f"{shown[select.key]}"
        for select in section.selects
    )
    return (lines, None)


def _selects_picker(
    section: SelectsSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    locale = context.locale
    pickers = _selects_controls(section, card, state, context)
    description = _localized(section.picker_description, locale) or ""
    return _picker_screen(card, section, locale, pickers, description), None


def _selects_controls(
    section: SelectsSection,
    card: CardStep,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Control, ...]:
    return tuple(
        select_picker(
            card.key,
            select,
            state.get(select.key),
            context.locale,
            required=select.key in card.required_keys,
        )
        for select in section.selects
    )


def _selects_change(
    section: SelectsSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    picked = payload if isinstance(payload, Mapping) else {}
    keys = {select.key for select in section.selects}
    return {
        slot: stored_ids(picked_ids(values))
        for slot, values in picked.items()
        if slot in keys
    }


def _selects_cleared(section: SelectsSection) -> Changes:
    return {select.key: [] for select in section.selects}


def _design_preview(
    section: DesignSection,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: Context,
) -> Lines:
    locale = context.locale
    value = state.get(section.state.value or "")
    chosen = next(
        (design.label.get(locale) for design in section.designs if design.key == value),
        None,
    )
    drawn = context.previews.get(str(value)) if value else None
    return ((f"> {chosen or _formatted(value, None, locale)}",), drawn)


def _gallery(
    section: DesignSection,
    card: CardStep,
    index: int,
    state: Mapping[str, Any],
    context: Context,
) -> tuple[Screen | None, Changes | None]:
    gallery = design_gallery(card.key, section.designs, context)
    back = Button(text("buttons.back.label", context.locale), "picker_back")
    return Screen(components=(gallery,), buttons=(back,), flavour="components_v2"), None


def _design_change(
    section: DesignSection,
    payload: Any,
    state: Mapping[str, Any],
    session: FormSession,
    context: Context,
) -> Changes:
    design = str(payload[0] if isinstance(payload, (list, tuple)) else payload)
    known = {candidate.key for candidate in section.designs}
    return {section.state.value or "": design} if design in known else {}


SECTION_KINDS: dict[str, SectionKind] = {
    "title-content": SectionKind(
        _title_content_modal,
        _title_content_change,
        _title_content_preview,
        _title_content_reset,
        _title_content_custom,
    ),
    "file-upload": SectionKind(
        _image_modal, _image_change, _image_preview, _image_reset, _image_custom
    ),
    "channel-select": SectionKind(_channel_picker, _picked, controls=_channel_controls),
    "user-select": SectionKind(
        _member_picker, _picked, _member_preview, controls=_member_controls
    ),
    "value-select": SectionKind(
        _value_picker, _value_change, _option_preview, controls=_options_select
    ),
    "button-options": SectionKind(
        _option_buttons, _option_pressed, _option_preview, controls=_options_select
    ),
    "boolean-toggle": SectionKind(
        _toggle, _toggled, _boolean_preview, _switched_off, controls=_yes_and_no
    ),
    "modal-input": SectionKind(_fields_modal, _fields_change, _fields_preview),
    "multi-select": SectionKind(
        _values_picker, _values_change, _values_preview, controls=_values_select
    ),
    "selects": SectionKind(
        _selects_picker,
        _selects_change,
        _selects_preview,
        _selects_cleared,
        controls=_selects_controls,
    ),
    "list": SectionKind(_opened_by_the_form, _unchanged, _list_preview),
    "group": SectionKind(_opened_by_the_form, _unchanged, _group_preview),
    "design-select": SectionKind(
        _gallery, _design_change, _design_preview, controls=_options_select
    ),
}

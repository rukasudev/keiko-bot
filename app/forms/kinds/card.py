"""A Components V2 card: sections that each edit part of one answer.

The card's draft lives in the session as the parts of the card's own key;
every section change is a `Drafted` event, Done turns the draft into the
answers the card's fields declare.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.constants import KeikoIcons
from app.forms.definitions.schema import (
    BooleanToggleSection,
    ButtonOptionsSection,
    CardStep,
    ChannelSelectSection,
    FileUploadSection,
    ModalInputSection,
    MultiSelectSection,
    Option,
    Section,
    Text,
    TitleContentSection,
    ValueSelectSection,
)
from app.forms.engine.rules import Scope, evaluate
from app.forms.engine.screen import (
    Button,
    Card,
    CardHeader,
    ChoiceOption,
    FileInput,
    Input,
    OptionSelect,
    Picker,
    Screen,
    SectionView,
    TextInputs,
)
from app.forms.engine.session import Answer, FormSession
from app.forms.engine.summary import option_label
from app.forms.extensions.copy import text
from app.forms.extensions.formatters import format_boolean, format_value
from app.forms.extensions.transforms import transform
from app.forms.extensions.validators import ValidationContext, validator
from app.forms.kinds import Refusal
from app.forms.kinds.common import back_button, cancel_button, footer_of
from app.forms.kinds.context import RenderContext

ICONS: dict[str, str] = {
    "IMAGE_01": KeikoIcons.IMAGE_01,
    "IMAGE_02": KeikoIcons.IMAGE_02,
    "IMAGE_03": KeikoIcons.IMAGE_03,
    "BIRTHDAY_GIF": KeikoIcons.BIRTHDAY_GIF,
}
EMPTY: tuple[Any, ...] = (None, "", [])
WITH_OPTIONS = (ValueSelectSection, ButtonOptionsSection, MultiSelectSection)
WITH_MODE = (TitleContentSection, FileUploadSection)


def _localized(value: Any, locale: str) -> Any:
    return value.get(locale) if isinstance(value, Text) else value


def _seed_state(
    card: CardStep, session: FormSession, context: RenderContext
) -> dict[str, Any]:
    keys = card.state_keys()
    state: dict[str, Any] = {}
    for key in keys:
        value = session.raw(key)
        if isinstance(value, Mapping):
            value = value.get("value")
        if value is None and key in context.document:
            value = context.document.get(key)
        state[key] = value
    rule = transform(card.transform)
    if rule:
        stored = session.raw(rule.value_key)
        if stored is None:
            stored = context.document.get(rule.value_key)
        for part, part_value in rule.hydrate(stored).items():
            if part in keys and not state.get(part):
                state[part] = part_value
    return state


def initial_state(
    card: CardStep, session: FormSession, context: RenderContext
) -> dict[str, Any]:
    """The card state before any change: prior answers, the document, defaults."""
    state = _seed_state(card, session, context)
    for key, value in card.defaults.items():
        if state.get(key) is None:
            state[key] = _localized(value, context.locale)
    for section in card.sections:
        mode = section.state.mode
        if not mode or state.get(mode) in ("default", "custom"):
            continue
        payload = [
            k
            for k in (section.state.title, section.state.content, section.state.url)
            if k
        ]
        state[mode] = "custom" if any(state.get(k) for k in payload) else "default"
    return state


def state_of(
    card: CardStep, session: FormSession, context: RenderContext
) -> dict[str, Any]:
    """The current draft, or the initial state when nothing was changed yet."""
    draft = session.answers.get(card.key)
    if draft is not None and draft.parts:
        return dict(draft.parts)
    return initial_state(card, session, context)


def visible(section: Section, state: Mapping[str, Any]) -> bool:
    """True unless the section's rule hides it for this state."""
    return evaluate(section.visible_when, Scope(state))


def hidden_keys(card: CardStep, state: Mapping[str, Any]) -> set[str]:
    """State keys owned only by sections hidden for this state."""
    keys: set[str] = set()
    for section in card.sections:
        if not visible(section, state):
            keys.update(section.state.keys())
    return keys


def is_custom(section: Section, state: Mapping[str, Any]) -> bool:
    """True when a default-or-custom section holds a custom value."""
    if isinstance(section, TitleContentSection):
        return state.get(section.state.mode or "") == "custom"
    if isinstance(section, FileUploadSection):
        mode, url = section.state.mode or "", section.state.url or ""
        return state.get(mode) == "custom" and bool(state.get(url))
    return True


def _template_vars(
    card: CardStep, session: FormSession, context: RenderContext
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for name, var in card.context.items():
        if var.context == "server_name":
            resolved[name] = context.server_name
            continue
        value = session.raw(var.answer or "")
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


def _formatted(value: Any, style: str | None, locale: str) -> str:
    if value in EMPTY:
        return "-"
    return format_value(value, style, locale) if style else str(value)


def preview(
    section: Section,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: RenderContext,
) -> tuple[tuple[str, ...], str | None]:
    """The lines under a section heading, and a media url when it shows one."""
    locale = context.locale
    value = state.get(section.state.value or "")
    if isinstance(section, (ValueSelectSection, ButtonOptionsSection)):
        label = option_label(section.options, value, locale)
        return ((f"> {label or _formatted(value, section.style, locale)}",), None)
    if isinstance(section, MultiSelectSection):
        chosen = [
            str(v)
            for v in (
                value if isinstance(value, (list, tuple)) else [value] if value else []
            )
        ]
        labels = [
            o.label.get(locale) for o in section.options if str(o.value) in chosen
        ]
        return ((f"> {', '.join(labels) if labels else '—'}",), None)
    if isinstance(section, BooleanToggleSection):
        return ((f"> {format_boolean(bool(value), locale)}",), None)
    if isinstance(section, TitleContentSection):
        if is_custom(section, state):
            title = state.get(section.state.title or "") or ""
            content = state.get(section.state.content or "") or ""
        else:
            title = section.default.title.get(locale)
            content = section.default.content.get(locale)
        variables = _template_vars(card, session, context)
        lines = []
        if title:
            lines.append(f"> **{_fill(title, variables)}**")
        if content:
            lines.append(f"> {_fill(content, variables)}")
        return (tuple(lines), None)
    if isinstance(section, FileUploadSection):
        url = state.get(section.state.url or "") if is_custom(section, state) else None
        return ((), str(url) if url else None)
    return ((f"> {_formatted(value, section.style, locale)}",), None)


def _section_view(
    index: int,
    section: Section,
    state: Mapping[str, Any],
    card: CardStep,
    session: FormSession,
    context: RenderContext,
) -> SectionView:
    locale = context.locale
    label = section.label.get(locale)
    customize = section.customize_label.get(locale) if section.customize_label else ""
    lines, media = preview(section, state, card, session, context)
    if isinstance(section, WITH_MODE):
        custom = is_custom(section, state)
        badge_key = "badge-custom" if custom else "badge-default"
        badge = text(f"buttons.summary-card.{badge_key}", locale)
        heading = f"{section.icon} **{label}:** {badge}"
        buttons: tuple[Button, ...]
        if custom:
            buttons = (
                Button(text("buttons.summary-card.edit", locale), f"section:{index}"),
                Button(text("buttons.summary-card.reset", locale), f"reset:{index}"),
            )
        else:
            buttons = (Button(customize, f"section:{index}"),)
        return SectionView(index, section.key, heading, lines, buttons, media)
    edit_label = customize or text("buttons.summary-card.edit", locale)
    return SectionView(
        index,
        section.key,
        f"{section.icon} **{label}**",
        lines,
        (Button(edit_label, f"section:{index}"),),
        media,
    )


def _header(card: CardStep, session: FormSession, context: RenderContext) -> CardHeader:
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


def render(step: Any, session: FormSession, context: RenderContext) -> Screen:
    """The card with its visible sections, Done, Back and Cancel."""
    card: CardStep = step
    locale = context.locale
    state = state_of(card, session, context)
    sections = tuple(
        _section_view(index, section, state, card, session, context)
        for index, section in enumerate(card.sections)
        if visible(section, state)
    )
    buttons: list[Button] = [
        Button(text("buttons.summary-card.done", locale), "done", "success")
    ]
    if context.can_go_back:
        buttons.append(back_button(locale))
    buttons.append(cancel_button(locale))
    return Screen(
        components=(Card(card.key, _header(card, session, context), sections),),
        buttons=tuple(buttons),
        flavour="components_v2",
        layout_footer=footer_of(card, locale),
    )


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
    component: Any,
    description: str = "",
    buttons: Sequence[Button] = (),
) -> Screen:
    return Screen(
        title=_picker_title(section, locale),
        description=description,
        components=(component,) if component is not None else (),
        buttons=(*buttons, Button(text("buttons.back.label", locale), "picker_back")),
        flavour="components_v2",
        layout_footer=footer_of(card, locale),
    )


def _options(
    options: Sequence[Option], selected: Sequence[str], locale: str
) -> tuple[ChoiceOption, ...]:
    return tuple(
        ChoiceOption(
            o.label.get(locale),
            str(o.value),
            o.style or "secondary",
            str(o.value) in selected,
        )
        for o in options
    )


def _modal_title(section: Section, locale: str) -> str:
    modal = getattr_modal(section)
    title = modal.title.get(locale) if modal else section.label.get(locale)
    if not section.icon or title.lstrip().startswith(section.icon):
        return title
    return f"{section.icon} {title}".strip()


def getattr_modal(section: Section) -> Any:
    """The modal spec of a section that opens one, None otherwise."""
    if isinstance(section, (TitleContentSection, FileUploadSection, ModalInputSection)):
        return section.modal
    return None


def open_section(
    card: CardStep, index: int, session: FormSession, context: RenderContext
) -> tuple[Screen | None, Mapping[str, Any] | None]:
    """What pressing a section does: a screen to show, or a change to apply."""
    section = card.sections[index]
    locale = context.locale
    state = state_of(card, session, context)
    key = section.state.value or ""
    if isinstance(section, ChannelSelectSection):
        picker = Picker(
            card.key,
            "channel",
            text("buttons.components.select.channel-placeholder", locale),
            unique=True,
            slot=key,
            required=True,
        )
        return _picker_screen(card, section, locale, picker), None
    if isinstance(section, ValueSelectSection):
        single = OptionSelect(
            card.key,
            _picker_title(section, locale),
            _options(section.options, (), locale),
            1,
            1,
            slot=key,
        )
        return _picker_screen(
            card,
            section,
            locale,
            single,
            _localized(section.picker_description, locale) or "",
        ), None
    if isinstance(section, MultiSelectSection):
        chosen = [str(v) for v in (state.get(key) or [])]
        multiple = OptionSelect(
            card.key,
            _picker_title(section, locale),
            _options(section.options, chosen, locale),
            0,
            max(len(section.options), 1),
            slot=key,
        )
        return _picker_screen(
            card,
            section,
            locale,
            multiple,
            _localized(section.picker_description, locale) or "",
        ), None
    if isinstance(section, ButtonOptionsSection):
        buttons = tuple(
            Button(o.label.get(locale), f"pick:{i}", o.style or "secondary")
            for i, o in enumerate(section.options)
        )
        return _picker_screen(
            card,
            section,
            locale,
            None,
            _localized(section.picker_description, locale) or "",
            buttons,
        ), None
    if isinstance(section, BooleanToggleSection):
        return None, {key: not bool(state.get(key))}
    return _modal_screen(card, section, state, locale), None


def _modal_screen(
    card: CardStep, section: Section, state: Mapping[str, Any], locale: str
) -> Screen:
    if isinstance(section, TitleContentSection):
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
        )
    if isinstance(section, FileUploadSection):
        component = FileInput(
            card.key,
            text("buttons.file-upload.label", locale) or "Image",
            slot=section.key,
        )
        return Screen(
            modal_title=_modal_title(section, locale),
            components=(component,),
            flavour="modal",
        )
    assert isinstance(section, ModalInputSection)
    key = section.state.value or ""
    inputs: list[Input] = []
    for field in section.modal.fields:
        default = (
            str(state[key]) if field.key == key and state.get(key) is not None else None
        )
        placeholder = field.placeholder.get(locale) if field.placeholder else None
        inputs.append(
            Input(
                field.label.get(locale),
                field.key,
                placeholder or default,
                default,
                field.required,
                field.max_length or 40,
            )
        )
    return Screen(
        modal_title=_modal_title(section, locale),
        components=(TextInputs(card.key, tuple(inputs), slot=section.key),),
        flavour="modal",
    )


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
        if check.check(candidate, ValidationContext(answers=candidate)):
            changes[rule.key] = None


def _modal_change(
    section: Section, payload: Any, state: Mapping[str, Any]
) -> Mapping[str, Any] | Refusal:
    key = section.state.value or ""
    if isinstance(section, TitleContentSection):
        values = payload.get("inputs") if isinstance(payload, Mapping) else payload
        title, content = (list(values or []) + ["", ""])[:2]
        return {
            section.state.mode or "": "custom",
            section.state.title or "": title,
            section.state.content or "": content,
        }
    if isinstance(section, FileUploadSection):
        url = payload.get("url") if isinstance(payload, Mapping) else payload
        if not url:
            return {}
        return {section.state.mode or "": "custom", section.state.url or "": url}
    assert isinstance(section, ModalInputSection)
    values = payload.get("inputs") if isinstance(payload, Mapping) else [payload]
    value = list(values or [""])[0]
    check = validator(section.modal.validation)
    if check:
        error = check.check({**state, key: value}, ValidationContext(answers=state))
        if error:
            return Refusal(error)
    return {key: value}


def _choice_change(
    section: Section, payload: Any, state: Mapping[str, Any]
) -> Mapping[str, Any]:
    key = section.state.value or ""
    if isinstance(section, ButtonOptionsSection):
        index = int(payload) if str(payload).isdigit() else -1
        if 0 <= index < len(section.options):
            return {key: str(section.options[index].value)}
        return {key: str(payload)}
    if isinstance(section, MultiSelectSection):
        chosen = payload if isinstance(payload, (list, tuple)) else [payload]
        return {key: [str(v) for v in chosen]}
    value = payload[0] if isinstance(payload, (list, tuple)) else payload
    if isinstance(section, ValueSelectSection):
        changes: dict[str, Any] = {key: value}
        if str(state.get(key)) != str(value):
            _reset_dependents(section, state, value, changes)
        return changes
    return {key: str(value)}


def apply_change(
    card: CardStep,
    index: int,
    payload: Any,
    session: FormSession,
    context: RenderContext,
) -> Mapping[str, Any] | Refusal:
    """The state changes a section's payload produces, or a refusal."""
    section = card.sections[index]
    state = state_of(card, session, context)
    if isinstance(section, WITH_MODE + (ModalInputSection,)):
        return _modal_change(section, payload, state)
    if isinstance(section, BooleanToggleSection):
        key = section.state.value or ""
        return {key: not bool(state.get(key))}
    return _choice_change(section, payload, state)


def apply_drafts(
    card: CardStep,
    changes: Mapping[str, Any],
    session: FormSession,
    context: RenderContext,
) -> Mapping[str, Any] | Refusal:
    """What a section's select reported, through the section's own rules."""
    applied: dict[str, Any] = {}
    for slot, payload in changes.items():
        index = next(
            (i for i, s in enumerate(card.sections) if s.state.value == slot), None
        )
        if index is None:
            applied[slot] = payload
            continue
        change = apply_change(card, index, payload, session, context)
        if isinstance(change, Refusal):
            return change
        applied.update(change)
    return applied


def reset_section(card: CardStep, index: int) -> Mapping[str, Any]:
    """The state a section goes back to on Reset."""
    section = card.sections[index]
    if isinstance(section, TitleContentSection):
        return {
            section.state.mode or "": "default",
            section.state.title or "": None,
            section.state.content or "": None,
        }
    if isinstance(section, FileUploadSection):
        return {section.state.mode or "": "default", section.state.url or "": None}
    if isinstance(section, BooleanToggleSection):
        return {section.state.value or "": False}
    return {section.state.value or "": None}


def draft(
    card: CardStep,
    changes: Mapping[str, Any],
    session: FormSession,
    context: RenderContext,
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
        for key in section.state.keys():
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
    step: Any, payload: Any, session: FormSession, context: RenderContext
) -> Mapping[str, Answer] | Refusal:
    """Done: the draft becomes the answers the card's fields declare."""
    card: CardStep = step
    state = dict(state_of(card, session, context))
    hidden = hidden_keys(card, state)
    missing = [
        k for k in card.required_keys if k not in hidden and state.get(k) in EMPTY
    ]
    if missing:
        labels = required_labels(card, context.locale)
        joined = _join_labels([labels.get(k, k) for k in missing], context.locale)
        return Refusal("buttons.summary-card.required", {"fields": joined}, plain=True)
    rule = transform(card.transform)
    if rule:
        state[rule.value_key] = rule.serialize(state)
    answers: dict[str, Answer] = {card.key: Answer(None, state)}
    for field in card.fields:
        if field.key in state:
            answers[field.key] = Answer(state[field.key])
    return answers

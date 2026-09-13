"""The manager: the panel over a saved document and the screens it opens."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.constants import KeikoIcons, Style
from app.constants import ViewConstants as view_constants
from app.forms.definitions.schema import (
    ButtonOptionsSection,
    CardStep,
    CompositionStep,
    FormDefinition,
    MultiPickStep,
    MultiSelectSection,
    SingleChoiceStep,
    Step,
    UserPickStep,
    ValueSelectSection,
)
from app.forms.engine.documents import unwrap
from app.forms.engine.rules import Scope, evaluate
from app.forms.engine.screen import (
    Button,
    ChoiceOption,
    Field,
    Input,
    OptionSelect,
    Panel,
    PanelGroup,
    Picker,
    Screen,
    TextInputs,
)
from app.forms.extensions.copy import text
from app.forms.extensions.formatters import format_value
from app.forms.kinds.common import cancel_button, caption, confirm_button, label
from app.forms.kinds.context import PanelRow, RenderContext

SILENT = ("intro", "info", "review")
EMOJI_PREFIX = re.compile(r"^(?:[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍])+\s*")
BUTTONS_PER_ROW = 5
LIFECYCLE_EVENTS = {"pause": "paused", "unpause": "unpaused", "disable": "disabled"}


def step_titles(steps: Sequence[Step], locale: str) -> dict[str, str]:
    """Step key to title for every step the edit picker lists."""
    return {
        step.key: step.title.get(locale)
        for step in steps
        if not step.hidden and step.kind not in SILENT
    }


def _nested(steps: Sequence[Step], locale: str) -> dict[str, tuple[str, str | None]]:
    nested: dict[str, tuple[str, str | None]] = {}
    for step in steps:
        if isinstance(step, MultiPickStep):
            for select in step.selects:
                nested[select.key] = (select.label.get(locale), select.style)
        if isinstance(step, CardStep):
            for field in step.fields:
                nested[field.key] = (field.label.get(locale), field.style)
    return nested


def _labels(steps: Sequence[Step], locale: str) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    for step in steps:
        if isinstance(step, SingleChoiceStep) and step.designs:
            labels[step.key] = {d.key: d.label.get(locale) for d in step.designs}
        if isinstance(step, CardStep):
            for section in step.sections:
                options = (ValueSelectSection, ButtonOptionsSection, MultiSelectSection)
                if isinstance(section, options) and section.state.value:
                    labels[section.state.value] = {
                        str(o.value): o.label.get(locale) for o in section.options
                    }
    return labels


def icons(steps: Sequence[Step]) -> dict[str, str]:
    """Row key to the icon its own YAML declares, first hit wins."""
    found: dict[str, str] = {}

    def claim(key: str | None, icon: str | None) -> None:
        if key and icon and key not in found:
            found[key] = icon

    for step in steps:
        if isinstance(step, CardStep):
            for section in step.sections:
                for state_key in section.state.keys():
                    claim(state_key, section.icon)
                claim(section.key, section.icon)
        if isinstance(step, MultiPickStep):
            for select in step.selects:
                claim(select.key, select.icon)
        claim(step.key, step.emoji)
    return found


def groups(steps: Sequence[Step], locale: str) -> dict[str, dict[str, str | None]]:
    """Row key to the step that owns it, localized."""
    found: dict[str, dict[str, str | None]] = {}
    for step in steps:
        header = step.header if isinstance(step, CardStep) else None
        title = header.title.get(locale) if header else step.title.get(locale)
        group = {
            "group": step.key,
            "group_title": title,
            "group_icon": step.emoji or (header.title_emoji if header else None),
        }
        keys: list[str] = []
        if isinstance(step, CardStep):
            keys += [field.key for field in step.fields]
        if isinstance(step, MultiPickStep):
            keys += [select.key for select in step.selects]
        if isinstance(step, CompositionStep):
            keys.append(step.key)
        for key in keys:
            found.setdefault(key, group)
    return found


def _composition_items(
    composition: CompositionStep, items: Sequence[Any], locale: str
) -> list[dict[str, Any]]:
    titles: dict[str, str] = {}
    for step in composition.steps:
        if isinstance(step, CardStep):
            titles.update({f.key: f.label.get(locale) for f in step.fields})
        if step.kind not in SILENT:
            titles.setdefault(step.key, step.title.get(locale))
    rows = []
    for item in items:
        row: dict[str, Any] = {}
        for key, entry in dict(item).items():
            if isinstance(entry, Mapping):
                row[key] = {
                    **entry,
                    "title": titles.get(key) or entry.get("title") or key,
                }
            else:
                row[key] = {"value": entry, "title": titles.get(key) or key}
        rows.append(row)
    return rows


def panel_rows(
    definition: FormDefinition, document: Mapping[str, Any], locale: str
) -> tuple[PanelRow, ...]:
    """The saved settings as panel rows, in document order."""
    steps = definition.steps
    titles = step_titles(steps, locale)
    nested = _nested(steps, locale)
    labels = _labels(steps, locale)
    icon_by_key, group_by_key = icons(steps), groups(steps, locale)
    values = {key: unwrap(value) for key, value in document.items()}
    rows: list[PanelRow] = []
    for key, value in document.items():
        title = titles.get(key) or (nested[key][0] if key in nested else None)
        if not title:
            continue
        step = next((s for s in steps if s.key == key), None)
        if step is not None and not evaluate(step.when, Scope(values)):
            continue
        if key in labels and isinstance(value, str):
            value = labels[key].get(value, value)
        style = nested[key][1] if key in nested else None
        if isinstance(value, Mapping) and value.get("style") == "composition":
            composition = definition.composition
            items = value.get("values") or []
            value = (
                _composition_items(composition, items, locale) if composition else items
            )
            style = "composition"
        group = group_by_key.get(key, {})
        rows.append(
            PanelRow(
                key=key,
                title=title,
                value=value,
                style=style,
                icon=icon_by_key.get(key),
                group=group.get("group"),
                group_title=group.get("group_title"),
                group_icon=group.get("group_icon"),
            )
        )
    return tuple(rows)


def without_leading_emoji(value: str) -> str:
    """A saved option value without the emoji its button label carries."""
    return EMOJI_PREFIX.sub("", value, count=1) or value


def labelled(title: str, value: str, separator: str = " ") -> str:
    """`Title: value`, without doubling punctuation after a question mark."""
    punctuation = "" if title.rstrip().endswith(("?", ":")) else ":"
    return f"**{title}{punctuation}**{separator}{value}"


def _composition_lines(values: Sequence[Mapping[str, Any]], locale: str) -> list[str]:
    limit = view_constants.COMPOSITION_PREVIEW_LIMIT
    lines = []
    for index, item in enumerate(values[:limit], start=1):
        lines.append(f"**#{index}**")
        for entry in item.values():
            if not isinstance(entry, Mapping) or entry.get("hidden"):
                continue
            value = format_value(entry.get("value"), entry.get("style"), locale) or "-"
            lines.append(labelled(entry.get("title") or "", str(value).strip()))
    if len(values) > limit:
        more = text("commands.resume.more", locale).replace(
            "$count", str(len(values) - limit)
        )
        lines.append(more)
    return lines


def row_value(row: PanelRow, locale: str) -> tuple[str, bool]:
    """(text, is_block); a value is a block only when it is a list of items."""
    values, style = row.value, row.style
    if isinstance(values, Mapping):
        style = values.get("style")
        values = values.get("values", "-")
    empty = text("commands.resume.empty", locale) or "-"
    if style == "composition":
        lines = _composition_lines(values or [], locale)
        return "\n".join(lines) or empty, True
    if isinstance(values, (list, tuple)) and style in ("bullet", "numbered", "code"):
        return format_value(list(values), "bullet", locale).lstrip("\n"), True
    formatted = format_value(values, style, locale)
    return without_leading_emoji(str(formatted or empty)), False


def _same_text(first: str, second: str) -> bool:
    def normalize(value: str) -> str:
        return without_leading_emoji(value or "").strip().casefold()

    return bool(normalize(first)) and normalize(first) == normalize(second)


def panel_groups(
    rows: Sequence[PanelRow], panel_title: str, locale: str
) -> tuple[PanelGroup, ...]:
    """Rows bucketed by the step that owns them, each rendered as its lines."""
    buckets: list[tuple[str | None, str | None, str, list[PanelRow]]] = []
    for row in rows:
        if row.hidden:
            continue
        bucket = next((b for b in buckets if b[0] == row.group), None)
        if bucket is None:
            bucket = (row.group, row.group_title, row.group_icon or "", [])
            buckets.append(bucket)
        bucket[3].append(row)
    result = []
    for key, title, icon, members in buckets:
        heading = ""
        if title and not _same_text(title, panel_title):
            heading = f"### {icon} {title}".replace("###  ", "### ")
        lines = [heading] if heading else []
        for row in members:
            value, is_block = row_value(row, locale)
            if heading and len(members) == 1 and row.title == title:
                lines.append(value)
                continue
            lines.append(labelled(row.title, value, "\n" if is_block else " "))
        result.append(PanelGroup(key, heading, tuple(lines)))
    return tuple(result)


def _button(key: str, action: str, emoji: str, locale: str) -> Button:
    return Button(
        label(key, locale), action, "secondary", emoji, description=caption(key, locale)
    )


def panel_buttons(
    definition: FormDefinition,
    document: Mapping[str, Any],
    context: RenderContext,
    sections_cover: bool,
) -> tuple[Button, ...]:
    """Every manager button in the order the panel shows them."""
    locale = context.locale
    buttons: list[Button] = []
    if not sections_cover:
        buttons.append(_button("edit", "edit", "📝", locale))
    if context.enabled:
        buttons.append(_button("pause", "lifecycle:pause", "⏸️", locale))
    else:
        buttons.append(_button("unpause", "lifecycle:unpause", "▶️", locale))
    buttons.append(_button("disable", "lifecycle:disable", "🚫", locale))
    composition = definition.composition
    if composition is not None:
        buttons.append(_button("add", "add", "➕", locale))
        stored = document.get(composition.key)
        count = len(stored.get("values") or []) if isinstance(stored, Mapping) else 0
        if count > 1:
            buttons.append(_button("remove", "remove", "🗑️", locale))
    buttons.append(_button("history", "aside:history", "📜", locale))
    buttons.extend(context.extra_buttons)
    buttons.append(_button("help", "aside:help", "🙋", locale))
    return tuple(buttons)


def intro_step(definition: FormDefinition) -> Step:
    """The first step, whose copy names the feature."""
    return definition.steps[0]


def panel_screen(
    definition: FormDefinition, document: Mapping[str, Any], context: RenderContext
) -> Screen:
    """The whole manager panel."""
    locale = context.locale
    first = intro_step(definition)
    title = first.title.get(locale)
    if not context.enabled:
        title += f" ({text('commands.command-events.paused.key', locale)})"
    rows = (
        context.panel_rows
        if context.panel_rows is not None
        else panel_rows(definition, document, locale)
    )
    grouped = panel_groups(rows, title, locale)
    sections_cover = bool(grouped) and all(group.key for group in grouped)
    panel = Panel(
        title=title,
        intro=first.description.get(locale),
        groups=grouped,
        info=context.panel_info,
        info_title=context.panel_info_title,
        thumbnail=KeikoIcons.IMAGE_01,
        edit_label=label("edit", locale),
    )
    return Screen(
        components=(panel,),
        buttons=panel_buttons(definition, document, context, sections_cover),
        flavour="components_v2",
        layout_footer=first.footer.get(locale) if first.footer else "",
    )


def panel_embed(definition: FormDefinition, locale: str) -> Screen:
    """The embed a screen opened from the panel starts from."""
    first = intro_step(definition)
    return Screen(
        title=first.title.get(locale),
        description=first.description.get(locale),
        footer=first.footer.get(locale) if first.footer else "",
        thumbnail=KeikoIcons.IMAGE_01,
        color=Style.BACKGROUND_COLOR,
    )


def uses_member_picker(composition: CompositionStep | None) -> bool:
    """True when the composition is keyed by a member chosen on a select."""
    if composition is None or not composition.items.unique_by:
        return False
    step = next(
        (s for s in composition.steps if s.key == composition.items.unique_by), None
    )
    return isinstance(step, UserPickStep)


def _item_value(item: Mapping[str, Any], key: str) -> Any:
    return unwrap(item.get(key))


def edit_options(
    definition: FormDefinition, document: Mapping[str, Any], locale: str
) -> tuple[ChoiceOption, ...]:
    """The choices of the edit picker: steps, or the composition's items."""
    values = {key: unwrap(value) for key, value in document.items()}
    options: list[ChoiceOption] = []
    composition = definition.composition
    for key, title in step_titles(definition.steps, locale).items():
        step = definition.step(key)
        if not evaluate(step.when, Scope(values)):
            continue
        if composition is not None and key == composition.key:
            if uses_member_picker(composition):
                options.append(ChoiceOption(title, key))
                continue
            stored = document.get(key)
            items = stored.get("values") or [] if isinstance(stored, Mapping) else []
            for index, item in enumerate(items):
                unique = composition.items.unique_by
                detail = str(_item_value(item, unique) or "") if unique else ""
                options.append(
                    ChoiceOption(
                        f"{title} #{index + 1}", f"{key}${index}", description=detail
                    )
                )
            continue
        options.append(ChoiceOption(title, key))
    return tuple(options)


def remove_options(
    definition: FormDefinition, document: Mapping[str, Any], locale: str
) -> tuple[ChoiceOption, ...]:
    """The choices of the remove picker: one per item, or the member picker."""
    composition = definition.composition
    if composition is None:
        return ()
    title = step_titles(definition.steps, locale).get(composition.key, composition.key)
    if uses_member_picker(composition):
        return (ChoiceOption(title, composition.key),)
    stored = document.get(composition.key)
    items = stored.get("values") or [] if isinstance(stored, Mapping) else []
    return tuple(
        ChoiceOption(f"{title} #{index + 1}", f"{composition.key}${index}")
        for index in range(len(items))
    )


def picker_screen(
    base: Screen, placeholder: str, options: tuple[ChoiceOption, ...], unique: bool
) -> Screen:
    """An embed with one select over `options`."""
    select = OptionSelect(
        "", placeholder, options, 1, 1 if unique else max(len(options), 1)
    )
    return Screen(
        title=base.title,
        description=base.description,
        footer=base.footer,
        thumbnail=base.thumbnail,
        color=base.color,
        components=(select,),
    )


def member_picker_screen(
    definition: FormDefinition, action: str, locale: str
) -> Screen:
    """The member select opened when a composition is keyed by member."""
    composition = definition.composition
    assert composition is not None and composition.items.unique_by
    step = composition.steps[
        [s.key for s in composition.steps].index(composition.items.unique_by)
    ]
    namespace = f"commands.command-events.{action}.member-picker"
    return Screen(
        title=text(f"{namespace}.title", locale),
        description=text(f"{namespace}.description", locale),
        footer=step.footer.get(locale) if step.footer else "",
        thumbnail=KeikoIcons.IMAGE_01,
        color=Style.BACKGROUND_COLOR,
        components=(
            Picker(
                "", "user", text("buttons.components.select.user-placeholder", locale)
            ),
        ),
        buttons=(confirm_button(locale), cancel_button(locale)),
    )


def confirmation_modal(action: str, locale: str) -> Screen:
    """The typed confirmation a lifecycle action asks for."""
    word = text(f"commands.command-events.{LIFECYCLE_EVENTS[action]}.action", locale)
    title = text("commands.confirmation-modal.title", locale)
    prompt = text("commands.confirmation-modal.desc", locale).replace(
        "$action", word.lower()
    )
    return Screen(
        modal_title=title,
        components=(
            TextInputs("", (Input(prompt, "word", max_length=len(word)),), slot=action),
        ),
        flavour="modal",
    )


def confirmation_word(action: str, locale: str) -> str:
    """The word the admin must type to confirm `action`."""
    return text(f"commands.command-events.{LIFECYCLE_EVENTS[action]}.action", locale)


def discard_screen(locale: str) -> Screen:
    """Keep or discard, on a message of its own."""
    return Screen(
        title=f"🚨 {text('errors.discard-settings-confirmation.title', locale)}",
        description=text("errors.discard-settings-confirmation.message", locale),
        color=Style.RED_COLOR,
        buttons=(
            Button(text("buttons.cancel.keep", locale), "keep", "success"),
            Button(text("buttons.cancel.discard", locale), "discard", "danger"),
        ),
    )


def help_fields(buttons: Sequence[Button]) -> tuple[Field, ...]:
    """One field per captioned button, for the help screen."""
    return tuple(
        Field(
            f"{button.emoji} {button.label}" if button.emoji else button.label,
            button.description,
        )
        for button in buttons
        if button.label and button.description and button.action != "aside:help"
    )

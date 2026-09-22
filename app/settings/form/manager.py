"""The manager: the panel over a saved document and the screens it opens."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.constants import Emojis, KeikoIcons, Style
from app.constants import ViewConstants as view_constants
from app.settings.form import events as ev
from app.settings.form.actions.action import (
    PanelRow,
    RenderContext,
    caption,
    confirm_button,
    label,
)
from app.settings.form.components import (
    Button,
    ChoiceOption,
    Field,
    Input,
    OptionSelect,
    Panel,
    PanelGroup,
    PanelPart,
    Picker,
    Screen,
    TextInputs,
)
from app.settings.form.conditions import EMPTY, Scope, evaluate
from app.settings.form.copy import text
from app.settings.form.effects import (
    Ack,
    Commit,
    Confirm,
    Finalize,
    Notice,
    OpenModal,
    Render,
    ShowError,
)
from app.settings.form.form_state import (
    AddItem,
    Answer,
    Edit,
    EditItem,
    Manage,
    Status,
    raw_value,
)
from app.settings.form.responses.responses import document_answers, item_answers

if TYPE_CHECKING:
    from app.settings.form.form import Engine
from app.settings.form.form_yaml import (
    ButtonOptionsSection,
    CardStep,
    CompositionStep,
    DesignSection,
    FormDefinition,
    IntroStep,
    MultiPickStep,
    MultiSelectSection,
    PanelGroupRef,
    SingleChoiceStep,
    Step,
    TextStep,
    UserPickStep,
    ValueSelectSection,
    When,
    options_for,
    owned_keys,
    produced_keys,
)
from app.settings.form.responses.responses import unwrap
from app.settings.form.responses.styles import empty_value, format_value

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
            labels[step.key] = {
                design.key: design.label.get(locale) for design in step.designs
            }
        if isinstance(step, CardStep):
            for section in step.sections:
                options = (ValueSelectSection, ButtonOptionsSection, MultiSelectSection)
                if isinstance(section, options) and section.state.value:
                    labels[section.state.value] = {
                        str(option.value): option.label.get(locale)
                        for option in section.options
                    }
                if isinstance(section, DesignSection) and section.state.value:
                    labels[section.state.value] = {
                        design.key: design.label.get(locale)
                        for design in section.designs
                    }
    return labels


def icons(steps: Sequence[Step]) -> dict[str, str]:
    """Row key to the icon its own YAML declares, first hit wins."""
    found: dict[str, str] = {}

    def claim(key: str | None, icon: str | None) -> None:
        if key and icon and key not in found:
            found[key] = icon

    for step in steps:
        for key, icon in _declared_icons(step):
            claim(key, icon)
    return found


def _declared_icons(step: Step) -> list[tuple[str | None, str | None]]:
    declared: list[tuple[str | None, str | None]] = []
    if isinstance(step, CardStep):
        for section in step.sections:
            declared += [(key, section.icon) for key in owned_keys(section)]
            declared.append((section.key, section.icon))
    if isinstance(step, MultiPickStep):
        declared += [(select.key, select.icon) for select in step.selects]
    declared.append((step.key, step.emoji))
    if isinstance(step, TextStep):
        declared += [(field.key, step.emoji) for field in step.fields]
    return declared


def groups(
    steps: Sequence[Step], locale: str, scope: Scope | None = None
) -> dict[str, dict[str, Any]]:
    """Row key to the step that owns it, or to the group its YAML declares."""
    found: dict[str, dict[str, Any]] = {}
    headings = _declared_headings(steps, locale, scope)
    for step in steps:
        if step.hidden or step.kind in SILENT:
            continue
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
        keys += [key for key in produced_keys(step) if key not in keys]

        for key in keys:
            found.setdefault(key, _declared_group(step, key, headings) or group)
    return found


def _ref_title(ref: PanelGroupRef, locale: str, scope: Scope | None) -> str | None:
    """The heading a group declares, with its active variant chosen."""
    title = ref.title
    for variant in ref.title_when:
        if scope is not None and evaluate(variant.when, scope):
            title = variant.text
            break
    return title.get(locale) if title else None


def _declared_headings(
    steps: Sequence[Step], locale: str, scope: Scope | None
) -> dict[str, tuple[str | None, str | None]]:
    """Group key to its heading and emoji, from every ref that names it."""
    found: dict[str, tuple[str | None, str | None]] = {}
    for step in steps:
        refs = [step.panel_group] if step.panel_group else []
        if isinstance(step, CardStep):
            refs += [field.panel_group for field in step.fields if field.panel_group]

        for ref in refs:
            title, emoji = found.get(ref.key, (None, None))
            found[ref.key] = (
                title or _ref_title(ref, locale, scope),
                emoji or ref.emoji,
            )
    return found


def _declared_group(
    step: Step,
    key: str,
    headings: Mapping[str, tuple[str | None, str | None]],
) -> dict[str, Any] | None:
    ref = step.panel_group
    if isinstance(step, CardStep):
        field = next((one for one in step.fields if one.key == key), None)
        ref = (field.panel_group if field else None) or ref
    if ref is None:
        return None
    title, emoji = headings.get(ref.key, (None, None))
    return {
        "group": ref.key,
        "group_title": title,
        "group_icon": emoji,
        "declared": "1",
        "order": ref.order,
        "own_screen": ref.own_screen,
    }


def section_gates(steps: Sequence[Step]) -> dict[str, When]:
    """Row key to the rule that decides whether its card section is shown."""
    found: dict[str, When] = {}
    for step in steps:
        if not isinstance(step, CardStep):
            continue
        for section in step.sections:
            if section.visible_when is None:
                continue
            for key in owned_keys(section):
                found.setdefault(key, section.visible_when)
    return found


def part_targets(steps: Sequence[Step]) -> dict[str, str]:
    """Row key to the `step/part` an Edit beside it opens, when it has its own."""
    found: dict[str, str] = {}
    for step in steps:
        if isinstance(step, CardStep):
            for section in step.sections:
                for key in owned_keys(section):
                    if step.edit_by_field or _own_group(step, key):
                        found.setdefault(key, f"{step.key}/{section.key}")
        if isinstance(step, MultiPickStep) and step.edit_by_field:
            for select in step.selects:
                found.setdefault(select.key, f"{step.key}/{select.key}")
    return found


def _own_group(step: Step, key: str) -> bool:
    """True when a card field is listed under a group its card does not own."""
    if not isinstance(step, CardStep):
        return False
    field = next((one for one in step.fields if one.key == key), None)
    return field is not None and field.panel_group is not None


def _edit_labels(steps: Sequence[Step], locale: str) -> dict[str, str]:
    """Row key to the label of the button that edits it, when it declares one."""
    found: dict[str, str] = {}
    for step in steps:
        if isinstance(step, CardStep):
            for field in step.fields:
                if field.edit_label is not None:
                    found.setdefault(field.key, field.edit_label.get(locale))
        if step.edit_label is not None:
            found.setdefault(step.key, step.edit_label.get(locale))
    return found


def _composition_items(
    composition: CompositionStep, items: Sequence[Any], locale: str
) -> list[dict[str, Any]]:
    titles: dict[str, str] = {}
    item_icons = icons(composition.steps)
    for step in composition.steps:
        if isinstance(step, CardStep):
            titles.update({field.key: field.label.get(locale) for field in step.fields})
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
                    "icon": item_icons.get(key),
                }
            else:
                row[key] = {
                    "value": entry,
                    "title": titles.get(key) or key,
                    "icon": item_icons.get(key),
                }
        rows.append(row)
    return rows


def _named_items(composition: CompositionStep, items: Sequence[Any]) -> str:
    """The items named by what makes each unique, for a one line summary."""
    unique = composition.items.unique_by or ""
    names = []
    for item in items:
        entry = dict(item).get(unique)
        value = entry.get("value") if isinstance(entry, Mapping) else entry
        if value:
            names.append(f"`{value}`")
    return ", ".join(names)


def _has_value(value: Any) -> bool:
    """A setting with something saved in it, envelope or not."""
    return unwrap(value) not in EMPTY


def panel_rows(
    definition: FormDefinition,
    document: Mapping[str, Any],
    locale: str,
    expanded: bool = False,
) -> tuple[PanelRow, ...]:
    """The saved settings as panel rows, in document order.

    A list under a heading that opens a screen of its own reads as its items'
    names on the panel, and in full on that screen.
    """
    steps = definition.steps
    titles = step_titles(steps, locale)
    nested = _nested(steps, locale)
    labels = _labels(steps, locale)
    icon_by_key = icons(steps)
    targets, edit_labels = part_targets(steps), _edit_labels(steps, locale)
    gates = section_gates(steps)
    hidden = {
        field.key
        for step in steps
        if isinstance(step, CardStep)
        for field in step.fields
        if field.hidden
    }
    values = {key: unwrap(value) for key, value in document.items()}
    group_by_key = groups(steps, locale, Scope(values))
    rows: list[PanelRow] = []

    for key, value in document.items():
        title = titles.get(key) or (nested[key][0] if key in nested else None)
        if not title:
            continue
        step = next((step for step in steps if step.key == key), None)
        if (
            step is not None
            and not evaluate(step.when, Scope(values))
            and not _has_value(value)
        ):
            continue
        if key in gates and not evaluate(gates[key], Scope(values)):
            continue
        if key in labels and isinstance(value, str):
            value = labels[key].get(value, value)
        style = nested[key][1] if key in nested else None
        per_item = False
        group = group_by_key.get(key, {})
        if isinstance(value, Mapping) and value.get("style") == "composition":
            composition = definition.composition
            items = value.get("values") or []
            if composition is not None and group.get("own_screen") and not expanded:
                value = _named_items(composition, items)
                style = None
            else:
                value = (
                    _composition_items(composition, items, locale)
                    if composition
                    else items
                )
                style = "composition"
                per_item = bool(composition and composition.edit_by_item)
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
                hidden=key in hidden,
                target=targets.get(key) or (key if group.get("declared") else None),
                per_item=per_item,
                group_declared=bool(group.get("declared")),
                edit_label=edit_labels.get(key),
                group_order=int(group.get("order") or 0),
                group_screen=bool(group.get("own_screen")),
            )
        )
    return tuple(rows)


def placed(
    rows: Sequence[PanelRow], steps: Sequence[Step], locale: str
) -> tuple[PanelRow, ...]:
    """Rows a feature built, given the icon and group of the step owning their key."""
    icon_by_key, group_by_key = icons(steps), groups(steps, locale)
    result = []
    for row in rows:
        group = group_by_key.get(row.key, {}) if row.key else {}
        if row.group is not None or not group:
            result.append(row)
            continue
        result.append(
            replace(
                row,
                icon=row.icon or icon_by_key.get(row.key),
                group=group.get("group"),
                group_title=group.get("group_title"),
                group_icon=group.get("group_icon"),
                group_order=int(group.get("order") or 0),
                group_screen=bool(group.get("own_screen")),
            )
        )
    return tuple(result)


def without_leading_emoji(value: str) -> str:
    """A saved option value without the emoji its button label carries."""
    return EMOJI_PREFIX.sub("", value, count=1) or value


def labelled(title: str, value: str, separator: str = " ") -> str:
    """`Title: value`, without doubling punctuation after a question mark."""
    punctuation = "" if title.rstrip().endswith(("?", ":")) else ":"
    return f"**{title}{punctuation}**{separator}{value}"


def _item_lines(item: Mapping[str, Any], locale: str) -> list[str]:
    lines = []
    for entry in item.values():
        if not isinstance(entry, Mapping) or entry.get("hidden"):
            continue
        value = format_value(
            entry.get("value"), entry.get("style"), locale
        ) or empty_value(locale)
        line = labelled(entry.get("title") or "", str(value).strip())
        lines.append(f"{entry.get('icon') or Emojis.FRISBEE_EMOJI} {line}")
    return lines


def _empty_composition(row: PanelRow) -> bool:
    """A list with no item has nothing its Edit could open."""
    return row.style == "composition" and not row.value


def _empty_list(members: Sequence[PanelRow]) -> bool:
    """A list block with no item has nothing its Edit could open."""
    return len(members) == 1 and _empty_composition(members[0])


def _item_groups(row: PanelRow, locale: str) -> list[PanelGroup]:
    items = list(row.value or [])
    if not items:
        empty = empty_value(locale)
        line = f"{row.icon or Emojis.FRISBEE_EMOJI} {labelled(row.title, empty)}"
        return [PanelGroup(None, "", (line,))]
    groups_of_items = []

    for index, item in enumerate(items):
        heading = f"### {row.title} #{index + 1}"
        lines = (heading, *_item_lines(item, locale))
        groups_of_items.append(PanelGroup(f"{row.key}${index}", heading, lines))
    return groups_of_items


def _composition_lines(values: Sequence[Mapping[str, Any]], locale: str) -> list[str]:
    limit = view_constants.COMPOSITION_PREVIEW_LIMIT
    lines = []

    for index, item in enumerate(values[:limit], start=1):
        lines.append(f"**#{index}**")
        lines += _item_lines(item, locale)
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
    empty = empty_value(locale)
    if isinstance(values, (list, tuple)) and not values and style != "composition":
        return empty, False
    if style == "composition":
        lines = _composition_lines(values or [], locale)
        if not lines:
            return empty, False
        return "\n".join(lines), True
    if isinstance(values, (list, tuple)) and style in ("bullet", "numbered", "code"):
        return format_value(list(values), "bullet", locale).lstrip("\n"), True
    formatted = str(format_value(values, style, locale) or empty)
    if formatted.startswith("\n"):
        return formatted.lstrip("\n"), True
    return without_leading_emoji(formatted), False


def _same_text(first: str, second: str) -> bool:
    def normalize(value: str) -> str:
        return without_leading_emoji(value or "").strip().casefold()

    return bool(normalize(first)) and normalize(first) == normalize(second)


Bucket = tuple[str | None, str | None, list[PanelRow]]


def _buckets(rows: Sequence[PanelRow]) -> list[Bucket]:
    """The visible rows grouped by the step that owns them, in the order shown."""
    buckets: list[Bucket] = []
    for row in rows:
        if row.hidden:
            continue
        bucket = next(
            (candidate for candidate in buckets if candidate[0] == row.group), None
        )
        if bucket is None:
            bucket = (row.group, row.group_title, [])
            buckets.append(bucket)
        bucket[2].append(row)
    buckets.sort(key=lambda bucket: bucket[2][0].group_order)
    return buckets


def _placed_alone(row: PanelRow) -> bool:
    """True when the row is a plain line, with no button of its own beside it."""
    if not row.target:
        return True
    return _empty_composition(row) and not row.group_declared


def _add_part(parts: dict[str, PanelPart], row: PanelRow, line: str) -> None:
    """Put the line under the row's own button; an empty list offers Add."""
    target = row.target or ""
    found = parts.get(target)
    if found is not None:
        parts[target] = replace(found, lines=(*found.lines, line))
        return
    adds = _empty_composition(row)
    parts[target] = PanelPart(
        target,
        (line,),
        row.edit_label,
        f"add:{target}" if adds else "",
        "➕" if adds else "✏️",
    )


def panel_groups(
    rows: Sequence[PanelRow], panel_title: str, locale: str
) -> tuple[PanelGroup, ...]:
    """Rows bucketed by the step that owns them, each rendered as its lines."""
    result = []
    for key, title, members in _buckets(rows):
        if len(members) == 1 and members[0].per_item:
            result.extend(_item_groups(members[0], locale))
            continue
        values = [(row, *row_value(row, locale)) for row in members]
        if _empty_list(members) or members[0].group_declared:
            key = None
        several = len(members) > 1 or any(is_block for _, _, is_block in values)
        heading = ""
        if several and title and not _same_text(title, panel_title):
            heading = f"### {title}"
        lines = [heading] if heading else []
        parts: dict[str, PanelPart] = {}
        apart = bool(members[0].group_screen)
        for row, value, is_block in values:
            if heading and len(members) == 1 and row.title == title:
                lines.append(value)
                continue
            line = labelled(row.title, value, "\n" if is_block else " ")
            line = f"{row.icon or Emojis.FRISBEE_EMOJI} {line}"
            if apart or _placed_alone(row):
                lines.append(line)
                continue
            _add_part(parts, row, line)
        if apart:
            result.append(
                PanelGroup(f"group:{members[0].group}", heading, tuple(lines))
            )
            continue
        result.append(PanelGroup(key, heading, tuple(lines), tuple(parts.values())))
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
        stored = document.get(composition.key)
        count = len(stored.get("values") or []) if isinstance(stored, Mapping) else 0

        if count < composition.items.max:
            buttons.append(_button("add", "add", "➕", locale))
        if count > 1:
            buttons.append(_button("remove", "remove", "🗑️", locale))
    buttons.append(_button("history", "aside:history", "📜", locale))
    buttons.extend(context.extra_buttons)
    buttons.append(_button("help", "aside:help", "🙋", locale))

    return tuple(buttons)


def intro_step(definition: FormDefinition) -> Step:
    """The first step, whose copy names the feature."""
    return definition.steps[0]


def covers_its_rows(group: PanelGroup) -> bool:
    """True when every line of the group has a button of its own beside it."""
    if group.key:
        return True
    body = [line for line in group.lines if line != group.heading]
    return bool(group.parts) and not body


def _reaches(target: str, buttons: set[str]) -> bool:
    if target in buttons or target.split("$", 1)[0] in buttons:
        return True
    return any(one.startswith(f"{target}/") for one in buttons)


def _all_shown(key: str, document: Mapping[str, Any]) -> bool:
    """True when a heading's own screen buttons every item saved under `key`.

    The screen draws only the first `GROUP_ITEMS_SHOWN` items, so a longer
    list still needs the panel's Edit to reach the rest.
    """
    stored = document.get(key)
    if not isinstance(stored, Mapping) or stored.get("style") != "composition":
        return True
    return len(stored.get("values") or []) <= view_constants.GROUP_ITEMS_SHOWN


def every_setting_has_a_button(
    definition: FormDefinition,
    document: Mapping[str, Any],
    groups: Sequence[PanelGroup],
    locale: str,
    rows: Sequence[PanelRow] = (),
) -> bool:
    """True when nothing the edit picker would list is left without a button."""
    buttons = {group.key for group in groups if group.key}
    buttons |= {part.target for group in groups for part in group.parts}
    apart = {
        str(group.key).split(":", 1)[1]
        for group in groups
        if group.key and str(group.key).startswith("group:")
    }
    buttons |= {
        row.key for row in rows if row.group in apart and _all_shown(row.key, document)
    }
    return (
        bool(groups)
        and all(covers_its_rows(group) for group in groups)
        and all(
            _reaches(option.value, buttons)
            for option in edit_options(definition, document, locale)
        )
    )


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
        placed(context.panel_rows, definition.steps, locale)
        if context.panel_rows is not None
        else panel_rows(definition, document, locale)
    )
    grouped = panel_groups(rows, title, locale)
    sections_cover = every_setting_has_a_button(
        definition, document, grouped, locale, rows
    )
    described = (
        first.manager_description
        if isinstance(first, IntroStep) and first.manager_description
        else first.description
    )
    panel = Panel(
        title=title,
        intro=described.get(locale),
        groups=grouped,
        info=context.panel_info,
        info_title=context.panel_info_title,
        thumbnail=KeikoIcons.IMAGE_01,
        edit_label=label("edit", locale),
        remove_label=label("remove-item", locale),
    )

    return Screen(
        components=(panel,),
        buttons=panel_buttons(definition, document, context, sections_cover),
        flavour="components_v2",
        layout_footer=first.footer.get(locale) if first.footer else "",
    )


def group_screen(
    definition: FormDefinition,
    document: Mapping[str, Any],
    context: RenderContext,
    key: str,
) -> Screen:
    """One heading's settings on a screen of their own, with their own buttons.

    A list becomes one block per item, each with Edit and Remove; a setting
    with declared options becomes the options themselves, as buttons that turn
    on and off where they are read.
    """
    locale = context.locale
    rows = [
        replace(row, group_screen=False)
        for row in panel_rows(definition, document, locale, expanded=True)
        if row.group == key
    ]
    title = next((row.group_title for row in rows if row.group_title), key)
    icon = next((row.group_icon for row in rows if row.group_icon), "")
    values = {name: unwrap(value) for name, value in document.items()}
    blocks: list[PanelGroup] = []
    for row in rows:
        blocks += _group_blocks(definition, row, locale)
    first = intro_step(definition)
    panel = Panel(
        title=f"{icon} {title}".strip(),
        intro=_group_intro(definition, key, locale, Scope(values)),
        groups=tuple(blocks),
        thumbnail=KeikoIcons.IMAGE_01,
        edit_label=label("edit", locale),
        remove_label=label("remove-item", locale),
    )
    return Screen(
        components=(panel,),
        buttons=_group_buttons(definition, document, rows, locale),
        flavour="components_v2",
        layout_footer=first.footer.get(locale) if first.footer else "",
    )


def _group_ref(definition: FormDefinition, key: str) -> PanelGroupRef | None:
    found: PanelGroupRef | None = None
    for step in definition.steps:
        refs = [step.panel_group]
        if isinstance(step, CardStep):
            refs += [field.panel_group for field in step.fields]
        for ref in refs:
            if ref is None or ref.key != key:
                continue
            if ref.description is not None:
                return ref
            found = found or ref
    return found


def _group_intro(
    definition: FormDefinition, key: str, locale: str, scope: Scope
) -> str:
    """What this heading is for, in the words the mode in force asks for."""
    ref = _group_ref(definition, key)
    if ref is None or ref.description is None:
        return ""
    described = ref.description
    for variant in ref.description_when:
        if evaluate(variant.when, scope):
            described = variant.text
    return described.get(locale)


def _group_blocks(
    definition: FormDefinition, row: PanelRow, locale: str
) -> list[PanelGroup]:
    if row.style == "composition":
        return _item_blocks(row, locale)
    icon = row.icon or Emojis.FRISBEE_EMOJI
    options = options_for(definition.steps, row.key)
    if options:
        chosen = [str(found) for found in _listed(unwrap(row.value))]
        choices = tuple(
            ChoiceOption(
                option.label.get(locale),
                str(option.value),
                selected=str(option.value) in chosen,
            )
            for option in options
        )
        heading = f"{icon} **{row.title}:**"
        return [
            PanelGroup(None, "", (heading,), choices=choices, choice_target=row.key)
        ]
    value, is_block = row_value(row, locale)
    line = f"{icon} {labelled(row.title, value, chr(10) if is_block else ' ')}"
    part = PanelPart(row.target or row.key, (line,), row.edit_label)
    return [PanelGroup(None, "", (), (part,))]


def _item_blocks(row: PanelRow, locale: str) -> list[PanelGroup]:
    """One block per item, with its own Edit and Remove under its lines.

    Discord refuses a message over forty components, so the screen shows the
    first few and says how many are left; the panel's own Edit and Remove
    still reach every one of them.
    """
    items = list(row.value or [])
    if not items:
        empty = text("commands.resume.empty", locale) or "-"
        line = f"{row.icon or Emojis.FRISBEE_EMOJI} {labelled(row.title, empty)}"
        return [PanelGroup(None, "", (line,))]
    shown = items[: view_constants.GROUP_ITEMS_SHOWN]
    blocks = []
    for index, item in enumerate(shown):
        target = f"{row.key}${index}"
        blocks.append(
            PanelGroup(
                None,
                "",
                tuple(_item_lines(item, locale)),
                actions=(
                    Button(label("edit", locale), f"edit:{target}", "secondary", "✏️"),
                    Button(
                        label("remove-item", locale),
                        f"remove_one:{target}",
                        "danger",
                        "🗑️",
                    ),
                ),
            )
        )
    if len(items) > len(shown):
        more = text("commands.resume.more", locale).replace(
            "$count", str(len(items) - len(shown))
        )
        blocks.append(PanelGroup(None, "", (more,)))
    return blocks


def _listed(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value] if value not in (None, "") else []


def _group_buttons(
    definition: FormDefinition,
    document: Mapping[str, Any],
    rows: Sequence[PanelRow],
    locale: str,
) -> tuple[Button, ...]:
    """One way to add to the list under this heading, and the way back."""
    buttons: list[Button] = []
    composition = definition.composition
    listed = composition is not None and any(row.key == composition.key for row in rows)
    if composition is not None and listed:
        stored = document.get(composition.key)
        count = len(stored.get("values") or []) if isinstance(stored, Mapping) else 0
        if count < composition.items.max:
            buttons.append(_button("add", f"add:{composition.key}", "➕", locale))
    buttons.append(Button(label("back", locale), "picker_back"))
    return tuple(buttons)


def remove_item_screen(locale: str, target: str) -> Screen:
    """Keep or remove one item of a list, on a message of its own."""
    return Screen(
        title=text("buttons.remove.confirm.title", locale),
        description=text("buttons.remove.confirm.message", locale),
        color=Style.RED_COLOR,
        buttons=(
            Button(text("buttons.cancel.keep", locale), "keep", "success"),
            Button(label("remove", locale), f"remove_item:{target}", "danger"),
        ),
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
        (step for step in composition.steps if step.key == composition.items.unique_by),
        None,
    )
    return isinstance(step, UserPickStep)


def edit_options(
    definition: FormDefinition, document: Mapping[str, Any], locale: str
) -> tuple[ChoiceOption, ...]:
    """The choices of the edit picker: steps, or the composition's items."""
    values = {key: unwrap(value) for key, value in document.items()}
    options: list[ChoiceOption] = []
    composition = definition.composition

    for key, title in step_titles(definition.steps, locale).items():
        step = definition.step(key)
        if not evaluate(step.when, Scope(values)) and not _has_value(document.get(key)):
            continue
        if composition is not None and key == composition.key:
            if uses_member_picker(composition):
                options.append(ChoiceOption(title, key))
                continue
            stored = document.get(key)
            items = stored.get("values") or [] if isinstance(stored, Mapping) else []

            for index, item in enumerate(items):
                unique = composition.items.unique_by
                detail = str(unwrap(item.get(unique)) or "") if unique else ""
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
    base: Screen,
    placeholder: str,
    options: tuple[ChoiceOption, ...],
    unique: bool,
    locale: str,
) -> Screen:
    """An embed with one select over `options` and a way back."""
    select = OptionSelect(
        "",
        placeholder,
        options,
        1,
        1 if unique else max(len(options), 1),
        action="target",
    )
    return Screen(
        title=base.title,
        description=base.description,
        footer=base.footer,
        thumbnail=base.thumbnail,
        color=base.color,
        components=(select,),
        buttons=(Button(label("back", locale), "picker_back"),),
    )


def member_picker_screen(
    definition: FormDefinition, action: str, locale: str
) -> Screen:
    """The member select opened when a composition is keyed by member."""
    composition = definition.composition
    assert composition is not None and composition.items.unique_by
    step = composition.steps[
        [step.key for step in composition.steps].index(composition.items.unique_by)
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
                "",
                "user",
                text("buttons.components.select.user-placeholder", locale),
                slot="member",
            ),
        ),
        buttons=(
            confirm_button(locale),
            Button(label("back", locale), "picker_back"),
        ),
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


def on_manager_confirmed(engine: Engine) -> None:
    """Confirm on the member picker: act on the member drafted there."""
    awaiting = engine.session.awaiting or ""
    if not awaiting.startswith("member:"):
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return
    drafted = engine.session.answers.get("member")
    chosen = list(drafted.raw) if drafted and isinstance(drafted.raw, list) else []

    if not chosen:
        engine.effects.append(ShowError("selection-required", {}, True, 5))
        return
    _choose_member(engine, str(chosen[0]))


def on_edit_requested(engine: Engine) -> None:
    """Edit: open the form again, seeded with what is saved."""
    event = engine.event
    assert isinstance(event, ev.EditRequested)

    if event.target and event.target.startswith("group:"):
        _open_group(engine, event.target.split(":", 1)[1])
        return

    if event.target and "/" in event.target:
        _open_part_edit(engine, event.target)
        return
    if event.target and "$" in event.target:
        index = int(event.target.split("$", 1)[1])
        seed = _item_seed(engine, index)

        if not seed:
            engine.effects.append(Notice("stale"))
            engine.rerender()
            return
        engine.open_child(EditItem(index), seed)
        return
    composition = engine.definition.composition
    items_target = composition is not None and event.target == composition.key

    if event.target and not items_target:
        engine.open_child(Edit((event.target,)), _seed_for_edit(engine))
        return
    if items_target and uses_member_picker(composition):
        _open_member_picker(engine, "edit")
        return

    base = panel_embed(engine.definition, engine.locale)
    document = (
        engine.context.document
        if isinstance(engine.session.mode, Manage)
        else _review_document(engine)
    )
    options = edit_options(engine.definition, document, engine.locale)

    if items_target:
        options = tuple(
            option for option in options if option.value.startswith(f"{event.target}$")
        )
    if not options:
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return
    placeholder = text("commands.command-events.edited.placeholder", engine.locale)
    unique = engine.definition.composition is not None
    engine.session = engine.session.with_status(Status.AWAITING, awaiting="edit")
    engine.effects.append(
        Render(picker_screen(base, placeholder, options, unique, engine.locale))
    )


def on_add_requested(engine: Engine) -> None:
    """Add: open the form again, empty, for one more item."""
    engine.open_child(AddItem(), {})


def on_remove_requested(engine: Engine) -> None:
    """Remove: ask which item goes, or take the only one."""
    event = engine.event
    assert isinstance(event, ev.RemoveRequested)

    if event.target:
        engine.session = engine.session.with_status(
            Status.AWAITING, awaiting="remove_item"
        )
        engine.effects.append(Confirm(remove_item_screen(engine.locale, event.target)))
        return
    base = panel_embed(engine.definition, engine.locale)
    document = (
        engine.context.document
        if isinstance(engine.session.mode, Manage)
        else _review_document(engine)
    )
    options = remove_options(engine.definition, document, engine.locale)

    if not options:
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return
    placeholder = text("commands.command-events.removed.placeholder", engine.locale)
    engine.session = engine.session.with_status(Status.AWAITING, awaiting="remove")
    engine.effects.append(
        Render(picker_screen(base, placeholder, options, True, engine.locale))
    )


def on_target_chosen(engine: Engine) -> None:
    """A picker answered: act on the item or field it names."""
    event = engine.event
    assert isinstance(event, ev.TargetChosen)
    awaiting = engine.session.awaiting or ""
    composition = engine.definition.composition
    value = event.value

    if (
        composition is not None
        and value == composition.key
        and uses_member_picker(composition)
    ):
        _open_member_picker(engine, awaiting)
        return
    if "$" in value:
        key, number = value.split("$", 1)
        index = int(number)

        if awaiting == "remove":
            _remove_item(engine, index)
        else:
            engine.open_child(EditItem(index), _item_seed(engine, index))
        return
    engine.open_child(Edit(tuple(value.split(","))), _seed_for_edit(engine))


def _open_group(engine: Engine, key: str) -> None:
    """The screen a heading opens, with the rows it holds.

    Only the manager offers it: the screen saves as it goes, which a setup has
    no business doing before the admin confirms.
    """
    if not isinstance(engine.session.mode, Manage):
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return
    screen = group_screen(
        engine.definition, engine.context.document, engine.render_context(), key
    )
    engine.session = engine.session.at(f"group:{key}")
    engine.effects.append(Render(screen))


def in_group_screen(engine: Engine) -> bool:
    """True while the admin is working inside one heading's own screen."""
    return bool((engine.session.cursor or "").startswith("group:"))


def _commit_quietly(engine: Engine, kind: str, payload: Mapping[str, Any]) -> None:
    """Write, then draw the same screen again instead of closing the session."""
    engine.session = engine.session.with_status(Status.COMMITTING, awaiting="quiet")
    engine.effects.append(Commit(kind, payload, quiet=True))


def on_remove_item_confirmed(engine: Engine) -> None:
    """The admin confirmed which item goes."""
    event = engine.event
    assert isinstance(event, ev.RemoveItemConfirmed)
    engine.session = engine.session.with_status(Status.ACTIVE)
    _, _, number = event.target.partition("$")

    if not number.isdigit():
        engine.rerender()
        return
    _remove_item(engine, int(number))


def on_option_toggled(engine: Engine) -> None:
    """A choice on the screen goes on or off, saves, and the screen stays."""
    event = engine.event
    assert isinstance(event, ev.OptionToggled)
    stored = unwrap(engine.context.document.get(event.target))
    chosen = [str(value) for value in _listed(stored)]

    if event.value in chosen:
        chosen = [value for value in chosen if value != event.value]
    else:
        chosen.append(event.value)
    engine.session = engine.session.with_status(Status.COMMITTING, awaiting="quiet")
    engine.effects.append(
        Commit("edit", {"answers": {event.target: Answer(chosen)}}, quiet=True)
    )


def _open_part_edit(engine: Engine, target: str) -> None:
    step_key, part = target.split("/", 1)
    step = next(
        (
            candidate
            for candidate in engine.definition.steps
            if candidate.key == step_key
        ),
        None,
    )
    declared = set(part_targets(engine.definition.steps).values())
    if isinstance(step, (CardStep, MultiPickStep)) and target in declared:
        engine.open_child(Edit((step_key,), part=part), _seed_for_edit(engine))
        return
    engine.effects.append(Notice("stale"))
    engine.rerender()


def _open_member_picker(engine: Engine, awaiting: str) -> None:
    action = "edited" if awaiting == "edit" else "removed"
    engine.session = engine.session.with_status(
        Status.AWAITING, awaiting=f"member:{awaiting}"
    )
    engine.effects.append(
        Render(member_picker_screen(engine.definition, action, engine.locale))
    )


def on_member_chosen(engine: Engine) -> None:
    """A member picker answered: add or remove that member."""
    event = engine.event
    assert isinstance(event, ev.MemberChosen)
    _choose_member(engine, event.user_id)


def on_item_removed(engine: Engine) -> None:
    """An item was confirmed for removal: take it out."""
    event = engine.event
    assert isinstance(event, ev.ItemRemoved)
    _remove_item(engine, event.index)


def on_child_finished(engine: Engine) -> None:
    """A child form ended: fold its answers back into the panel."""
    event = engine.event
    assert isinstance(event, ev.ChildFinished)
    engine.session = engine.session.with_status(Status.ACTIVE)

    if event.cancelled:
        engine.rerender()
        return
    answers = {
        key: answer
        for key, answer in event.answers.items()
        if isinstance(answer, Answer)
    }

    if event.child_mode == "edit":
        _edit_finished(engine, answers)
        return
    item = answers
    if isinstance(engine.session.mode, Manage):
        _manager_item_finished(engine, event, item)
        return
    engine.set_items(engine.merge_item(item, event.index))
    cursor = engine.session.cursor
    step = engine.step(cursor)

    if isinstance(step, CompositionStep) and event.child_mode == "add_item":
        engine.advance(engine.index(cursor))
        return
    engine.show(engine.review_key() or cursor or "")


def on_lifecycle(engine: Engine) -> None:
    """A lifecycle button: ask before turning the feature on or off."""
    event = engine.event
    assert isinstance(event, ev.Lifecycle)
    engine.session = engine.session.with_status(Status.AWAITING, awaiting=event.action)
    engine.effects.append(
        OpenModal(
            confirmation_modal(event.action, engine.locale),
            "word",
            event.action,
        )
    )


def on_lifecycle_confirmed(engine: Engine) -> None:
    """The lifecycle was confirmed: apply it and redraw."""
    event = engine.event
    assert isinstance(event, ev.LifecycleConfirmed)
    expected = confirmation_word(event.action, engine.locale)
    if event.word.strip().lower() != expected.lower():
        engine.session = engine.session.with_status(Status.ACTIVE)
        engine.effects.append(Ack())
        return
    engine.commit(event.action, {})


def _seed_for_edit(engine: Engine) -> dict[str, Answer]:
    if isinstance(engine.session.mode, Manage):
        return document_answers(engine.context.document)
    return dict(engine.session.answers)


def _review_document(engine: Engine) -> dict[str, Any]:
    from app.settings.form.responses.responses import to_document

    return to_document(engine.steps, engine.session.answers, engine.locale)


def _item_seed(engine: Engine, index: int) -> dict[str, Answer]:
    if isinstance(engine.session.mode, Manage):
        from app.settings.form.responses.responses import items_of

        composition = engine.definition.composition
        assert composition is not None
        items = items_of(engine.context.document, composition.key)
        return item_answers(items[index]) if 0 <= index < len(items) else {}
    answered = engine.composition_items()
    return dict(answered[index]) if 0 <= index < len(answered) else {}


def _choose_member(engine: Engine, user_id: str) -> None:
    composition = engine.definition.composition
    assert composition is not None and composition.items.unique_by
    unique = composition.items.unique_by
    awaiting = engine.session.awaiting or ""
    action = "edited" if awaiting.endswith("edit") else "removed"

    if isinstance(engine.session.mode, Manage):
        from app.settings.form.responses.responses import items_of, unwrap

        items = items_of(engine.context.document, composition.key)
        index = next(
            (
                index
                for index, item in enumerate(items)
                if str(unwrap(item.get(unique))) == user_id
            ),
            None,
        )
    else:
        index = next(
            (
                index
                for index, item in enumerate(engine.composition_items())
                if raw_value(item.get(unique)) == user_id
            ),
            None,
        )
    if index is None:
        engine.effects.append(
            ShowError(
                f"commands.command-events.{action}.member-picker.not-found",
                plain=True,
                delete_after=None,
            )
        )
        return
    if action == "removed":
        _remove_item(engine, index)
        return
    engine.open_child(EditItem(index), _item_seed(engine, index))


def _remove_item(engine: Engine, index: int) -> None:
    if isinstance(engine.session.mode, Manage):
        from app.settings.form.responses.responses import items_of

        composition = engine.definition.composition
        assert composition is not None
        items = items_of(engine.context.document, composition.key)
        removed = items[index] if 0 <= index < len(items) else {}
        payload = {"index": index, "item": removed}

        if in_group_screen(engine):
            _commit_quietly(engine, "remove_item", payload)
            return
        engine.commit("remove_item", payload)
        return
    remaining = list(engine.composition_items())
    if 0 <= index < len(remaining):
        del remaining[index]
    engine.set_items(remaining)
    engine.session = engine.session.with_status(Status.ACTIVE)
    engine.show(engine.session.cursor or "")


def _edit_finished(engine: Engine, answers: Mapping[str, Answer]) -> None:
    if isinstance(engine.session.mode, Manage):
        engine.commit("edit", {"answers": answers})
        return
    engine.session = engine.session.with_answers(answers)
    engine.show(engine.review_key() or engine.session.cursor or "")


def _manager_item_finished(
    engine: Engine, event: ev.ChildFinished, item: Mapping[str, Answer]
) -> None:
    from app.settings.form.responses.responses import items_of, unwrap

    composition = engine.definition.composition
    assert composition is not None
    unique = composition.items.unique_by
    if event.child_mode == "add_item" and unique:
        items = items_of(engine.context.document, composition.key)
        wanted = raw_value(item.get(unique))

        if any(str(unwrap(existing.get(unique))) == wanted for existing in items):
            engine.effects.append(
                ShowError("item-already-registered", delete_after=None)
            )
            engine.effects.append(Finalize("duplicate"))
            engine.session = engine.session.with_status(Status.COMPLETED)

            return
    kind = "add_item" if event.child_mode == "add_item" else "edit_item"
    payload = {"answers": item, "index": event.index}

    if in_group_screen(engine):
        _commit_quietly(engine, kind, payload)
        return
    engine.commit(kind, payload)

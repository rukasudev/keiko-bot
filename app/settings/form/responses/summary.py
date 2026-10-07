"""The answers as a list of titled values: the review, the panel, the document.

Nothing here is stored. Titles, labels and styles come from the definition at
the moment of rendering, so a session holds machine values only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.constants import Emojis
from app.constants import ViewConstants as view_constants
from app.settings.form.components import ChoiceOption, PanelGroup, PanelPart
from app.settings.form.conditions import EMPTY, Scope, evaluate
from app.settings.form.copy import text
from app.settings.form.form_state import Answer
from app.settings.form.form_yaml import (
    CardStep,
    CompositionStep,
    FormDefinition,
    MultiPickStep,
    Option,
    PanelGroupRef,
    SingleChoiceStep,
    Step,
    TextStep,
    UserPickStep,
    When,
    owned_keys,
    produced_keys,
)
from app.settings.form.responses.styles import empty_value, format_value
from app.settings.form.responses.transforms import transform

SILENT = ("intro", "info", "review")
EMOJI_PREFIX = re.compile(r"^(?:[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍])+\s*")


@dataclass(frozen=True)
class ResponseView:
    """One answered value the way a summary lists it."""

    key: str
    title: str
    value: Any
    style: str | None = None
    hidden: bool = False
    raw: Any = None

    @property
    def machine_value(self) -> Any:
        """The value a document stores: the raw one when a label was shown."""
        return self.value if self.raw is None else self.raw

    def as_item_entry(self) -> dict[str, Any]:
        """The shape one field takes inside a stored composition item."""
        entry: dict[str, Any] = {"value": self.value, "title": self.title}
        if self.style:
            entry["style"] = self.style
        if self.hidden:
            entry["hidden"] = True
        if self.raw is not None:
            entry["_raw_value"] = self.raw
        return entry


def unwrap(value: Any) -> Any:
    """The machine value inside a `{style, values}` or `{value}` envelope."""
    if isinstance(value, Mapping):
        if "values" in value:
            return value["values"]
        if "value" in value:
            return value.get("_raw_value", value["value"])
    return value


def listed(value: Any) -> list[Any]:
    """A stored value as a list, empty when nothing was ever chosen."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value] if value not in (None, "") else []


def option_label(options: Sequence[Option], value: Any, locale: str) -> str | None:
    """The label of `value` among `options`, None when it is not one."""
    for option in options:
        if str(option.value) == str(value):
            return option.label.get(locale)
    return None


def _labels_for(options: Sequence[Option], raw: Any, locale: str) -> Any:
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    labels = [option_label(options, value, locale) or value for value in values]
    return labels if isinstance(raw, (list, tuple)) else labels[0]


def _single_choice_views(
    step: SingleChoiceStep, answer: Answer, locale: str
) -> list[ResponseView]:
    title = step.title.get(locale)
    if step.designs:
        for design in step.designs:
            if design.key == answer.raw:
                return [
                    ResponseView(
                        step.key,
                        title,
                        design.label.get(locale),
                        None,
                        False,
                        answer.raw,
                    )
                ]
        return [ResponseView(step.key, title, answer.raw, None, False, None)]
    if step.styled_values and not step.style:
        labels = _labels_for(step.options, answer.raw, locale)
        return [ResponseView(step.key, title, labels, None, step.hidden, answer.raw)]
    return [ResponseView(step.key, title, answer.raw, step.style, step.hidden, None)]


def _text_views(
    step: TextStep, key: str, answer: Answer, locale: str
) -> list[ResponseView]:
    if key != step.key:
        field = next((field for field in step.fields if field.key == key), None)
        title = field.label.get(locale) if field else key
        return [ResponseView(key, title, answer.raw, None, False, None)]
    if step.fields and answer.raw is None:
        return []
    return [
        ResponseView(
            step.key, step.title.get(locale), answer.raw, step.style, step.hidden
        )
    ]


def _card_hidden_keys(card: CardStep) -> tuple[set[str], set[str]]:
    payload_keys: set[str] = set()
    mode_keys: set[str] = set()
    for section in card.sections:
        state = section.state
        if state.mode:
            mode_keys.add(state.mode)
            payload_keys.update(
                key for key in (state.title, state.content, state.url) if key
            )
    return payload_keys, mode_keys


def _card_options(card: CardStep) -> dict[str, tuple[Option, ...]]:
    return {
        section.state.value: section.choices()
        for section in card.sections
        if section.state.value and section.choices()
    }


def _card_field_view(
    card: CardStep, key: str, answer: Answer, locale: str
) -> list[ResponseView]:
    if key == card.key:
        return []
    payload_keys, mode_keys = _card_hidden_keys(card)
    field = next((field for field in card.fields if field.key == key), None)

    if field is None:
        return []
    value, raw = answer.raw, None
    label = option_label(_card_options(card).get(key, ()), value, locale)

    if label is not None:
        value, raw = label, answer.raw
    hidden = bool(
        field.hidden
        or key in payload_keys
        or (key in mode_keys and answer.raw != "custom")
    )
    return [ResponseView(key, field.label.get(locale), value, field.style, hidden, raw)]


def item_entries(
    steps: Sequence[Step], item: Mapping[str, Answer], locale: str
) -> dict[str, dict[str, Any]]:
    """A composition item as its stored dict: `{key: {value, title, ...}}`."""
    return {view.key: view.as_item_entry() for view in responses(steps, item, locale)}


def _composition_views(
    step: CompositionStep, answer: Answer, locale: str
) -> list[ResponseView]:
    items = [item_entries(step.steps, item, locale) for item in (answer.raw or ())]
    return [ResponseView(step.key, step.title.get(locale), items, "composition")]


def _producer(steps: Sequence[Step], key: str) -> tuple[Step, str] | None:
    for step in steps:
        if step.key == key:
            return step, "own"
        if isinstance(step, MultiPickStep):
            if any(select.key == key for select in step.selects):
                return step, "select"
        if isinstance(step, CardStep) and key in {field.key for field in step.fields}:
            return step, "field"
        if isinstance(step, TextStep) and key in {field.key for field in step.fields}:
            return step, "field"
    return None


def _views_for(
    step: Step, role: str, key: str, answer: Answer, locale: str
) -> list[ResponseView]:
    if isinstance(step, MultiPickStep) and role == "select":
        select = next(select for select in step.selects if select.key == key)
        return [ResponseView(key, select.label.get(locale), answer.raw, select.style)]
    if isinstance(step, CardStep):
        return _card_field_view(step, key, answer, locale)
    if isinstance(step, TextStep):
        return _text_views(step, key, answer, locale)
    if isinstance(step, SingleChoiceStep):
        return _single_choice_views(step, answer, locale)
    if isinstance(step, CompositionStep):
        return _composition_views(step, answer, locale)
    if step.kind in ("intro", "info", "review"):
        return []
    return [
        ResponseView(
            step.key, step.title.get(locale), answer.raw, step.style, step.hidden
        )
    ]


def responses(
    steps: Sequence[Step], answers: Mapping[str, Answer], locale: str
) -> tuple[ResponseView, ...]:
    """Every answer of `answers` the way a summary lists it, in declaration order."""
    views: list[ResponseView] = []
    for key in _declared_order(steps, answers):
        found = _producer(steps, key)
        if found is None:
            continue
        step, role = found
        views.extend(_views_for(step, role, key, answers[key], locale))
    return tuple(views)


def _declared_order(steps: Sequence[Step], answers: Mapping[str, Answer]) -> list[str]:
    """The answered keys in the order the definition declares them."""
    ordered = [key for step in steps for key in produced_keys(step) if key in answers]
    return ordered + [key for key in answers if key not in ordered]


def configured_steps(
    steps: Sequence[Step], answers: Mapping[str, Answer]
) -> tuple[str, ...]:
    """The steps a save configured, in declaration order, named by key only."""
    configured: list[str] = []
    for step in steps:
        if step.kind in ("intro", "info", "review") or step.hidden:
            continue
        if any(_filled(answers.get(key)) for key in produced_keys(step)):
            configured.append(step.key)
    return tuple(configured)


def _filled(answer: Answer | None) -> bool:
    return answer is not None and answer.raw not in (None, "", [], (), {})


def transformed_value(step: TextStep | CardStep, parts: Mapping[str, Any]) -> Any:
    """The stored value of a step's parts under its transform, if any."""
    rule = transform(step.transform)
    return rule.serialize(parts) if rule else None


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


def _step_titles(steps: Sequence[Step], locale: str) -> dict[str, str]:
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
                if section.state.value and section.choices():
                    labels[section.state.value] = {
                        str(option.value): option.label.get(locale)
                        for option in section.choices()
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
    titles = _step_titles(steps, locale)
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


def item_lines(item: Mapping[str, Any], locale: str) -> list[str]:
    """One line per shown entry of a stored list item, led by its icon."""
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
        lines = (heading, *item_lines(item, locale))
        groups_of_items.append(PanelGroup(f"{row.key}${index}", heading, lines))
    return groups_of_items


def _composition_lines(values: Sequence[Mapping[str, Any]], locale: str) -> list[str]:
    limit = view_constants.COMPOSITION_PREVIEW_LIMIT
    lines = []

    for index, item in enumerate(values[:limit], start=1):
        lines.append(f"**#{index}**")
        lines += item_lines(item, locale)
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

    for key, title in _step_titles(definition.steps, locale).items():
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
    title = _step_titles(definition.steps, locale).get(composition.key, composition.key)
    if uses_member_picker(composition):
        return (ChoiceOption(title, composition.key),)
    stored = document.get(composition.key)
    items = stored.get("values") or [] if isinstance(stored, Mapping) else []
    return tuple(
        ChoiceOption(f"{title} #{index + 1}", f"{composition.key}${index}")
        for index in range(len(items))
    )

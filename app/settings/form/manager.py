"""The manager: the panel over a saved document and the screens it opens."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from app.constants import KeikoIcons, Style
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
    Picker,
    Screen,
    TextInputs,
)
from app.settings.form.conditions import Scope, evaluate
from app.settings.form.copy import text
from app.settings.form.effects import (
    Ack,
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
    FormDefinition,
    MultiPickStep,
    MultiSelectSection,
    SingleChoiceStep,
    Step,
    UserPickStep,
    ValueSelectSection,
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
        step = next((step for step in steps if step.key == key), None)
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
            value = format_value(
                entry.get("value"), entry.get("style"), locale
            ) or empty_value(locale)
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
    empty = empty_value(locale)
    if isinstance(values, (list, tuple)) and not values and style != "composition":
        return empty, False
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
        bucket = next(
            (candidate for candidate in buckets if candidate[0] == row.group), None
        )
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
    described = first.description
    panel = Panel(
        title=title,
        intro=described.get(locale),
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
    base = panel_embed(engine.definition, engine.locale)
    document = (
        engine.context.document
        if isinstance(engine.session.mode, Manage)
        else _review_document(engine)
    )
    options = remove_options(engine.definition, document, engine.locale)
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
        engine.commit("remove_item", {"index": index, "item": removed})

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
    engine.commit(kind, {"answers": item, "index": event.index})

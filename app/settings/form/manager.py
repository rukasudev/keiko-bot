"""The manager: the panel over a saved document, its screens and what its buttons do.

It decides on its own, over the sessions the panel opens; the form never
names it. Its Edit, Add and Remove open the form as a child session, and
what the child answers comes back here to be saved.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.constants import Emojis, KeikoIcons
from app.constants import ViewConstants as view_constants
from app.settings.form import events as ev
from app.settings.form.actions.action import Context, caption, label
from app.settings.form.components import (
    Button,
    ChoiceOption,
    Input,
    Panel,
    PanelGroup,
    PanelPart,
    Screen,
    TextInputs,
)
from app.settings.form.conditions import Scope, chosen_text
from app.settings.form.copy import text
from app.settings.form.effects import (
    Ack,
    Commit,
    Finalize,
    Notice,
    OpenModal,
    Render,
    ShowError,
)
from app.settings.form.form import (
    Decision,
    Engine,
    confirm_removal,
    on_add_requested,
    on_aside,
    on_child_finished,
    on_commit_failed,
    on_edit_requested,
    on_expired,
    on_item_removed,
    on_keep_editing,
    on_member_chosen,
    on_member_confirmed,
    on_removal_confirmed,
    on_remove_requested,
    on_saved,
    on_screen_requested,
    on_target_chosen,
    on_targets_chosen,
    open_member_picker,
    settle,
)
from app.settings.form.form_state import Answer, FormSession, Status, raw_value
from app.settings.form.form_yaml import (
    CardStep,
    CompositionStep,
    FormDefinition,
    IntroStep,
    PanelGroupRef,
    options_for,
    produced_keys,
)
from app.settings.form.responses.responses import (
    document_answers,
    item_answers,
    items_of,
)
from app.settings.form.responses.summary import (
    PanelRow,
    every_setting_has_a_button,
    item_groups,
    item_heading,
    item_lines,
    item_name,
    kept_items,
    labelled,
    list_adder,
    listed,
    panel_groups,
    panel_rows,
    placed,
    row_value,
    step_title,
    titled_items,
    unwrap,
    without_leading_emoji,
)

LIFECYCLE_EVENTS = {"pause": "paused", "unpause": "unpaused", "disable": "disabled"}


@dataclass(frozen=True)
class PanelPage:
    """One page of a list the panel turns through: its title, a line under it, items."""

    title: str
    lines: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class FoundItem:
    """What a feature adds to the screen of an item a search found: its lines."""

    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class PanelExtras:
    """What a feature adds to its panel: rows, pages, found items, buttons, state."""

    rows: Sequence[PanelRow] | None = None
    info: str = ""
    info_title: str = ""
    info_action: Button | None = None
    extra_buttons: Sequence[Button] = ()
    enabled: bool = True
    pages: tuple[PanelPage, ...] = ()
    found: Mapping[str, FoundItem] = field(default_factory=dict)


@dataclass(frozen=True)
class ItemRemovalRequested(ev.Event):
    """Remove was pressed beside one item of a list, or on the item a search found."""

    target: str = ""


@dataclass(frozen=True)
class OptionToggled(ev.Event):
    """One option of a multiple choice was turned on or off, on the screen."""

    target: str = ""
    value: str = ""
    turned_on: bool = False


@dataclass(frozen=True)
class PageTurned(ev.Event):
    """◀ or ▶ was pressed under the pages of the panel."""

    index: int = 0


@dataclass(frozen=True)
class SearchRequested(ev.Event):
    """The admin asked to find one item of the list by its member."""


@dataclass(frozen=True)
class FromSetup(ev.Event):
    """The panel opened on the message of the setup that just saved the feature."""


@dataclass(frozen=True)
class Lifecycle(ev.Event):
    """Pause, unpause or disable was pressed on the panel."""

    action: str = ""


@dataclass(frozen=True)
class LifecycleConfirmed(ev.Event):
    """The confirmation modal of a lifecycle action was submitted."""

    action: str = ""
    word: str = ""


class Manager(Engine):
    """A manage session's decision: the panel and what each of its buttons does."""

    def __init__(
        self,
        definition: FormDefinition,
        session: FormSession,
        event: ev.Event,
        context: Context,
        panel: PanelExtras,
    ) -> None:
        super().__init__(definition, session, event, context)
        self.panel = panel

    @property
    def document(self) -> Mapping[str, Any]:
        """The saved document the panel shows."""
        return self.context.document

    def rerender(self) -> None:
        """Draw the panel again, or the screen of its own the admin is on."""
        cursor = self.session.cursor or ""
        place, _, arg = cursor.partition(":")
        if place == "group":
            screen = group_screen(
                self.definition, self.document, self.locale, arg, self.panel
            )
            self.effects.append(Render(screen))
            return
        if place == "found" and arg.isdigit() and self.found_screen(int(arg)):
            return
        self.effects.append(Render(self.panel_screen()))

    def panel_screen(self) -> Screen:
        """The whole panel over the saved document, on the page the admin turned to."""
        cursor = self.session.cursor or ""
        place, _, arg = cursor.partition(":")
        page = int(arg) if place == "page" and arg.isdigit() else 0
        return panel_screen(
            self.definition, self.document, self.locale, self.panel, page
        )

    def found_screen(self, index: int) -> bool:
        """Draw the item a search found, False when it is no longer saved."""
        composition = self.definition.composition
        items = self.stored_items()
        if composition is None or not 0 <= index < len(items):
            return False

        unique = composition.items.unique_by or ""
        member = str(unwrap(items[index].get(unique)))
        screen = found_screen(
            self.definition,
            composition,
            items[index],
            index,
            self.locale,
            self.panel.found.get(member),
            self.context.member_pictures.get(member) or "",
        )
        self.effects.append(Render(screen))
        return True

    def open_found(self, index: int) -> None:
        """Show the item the search found, with its own Edit and Remove."""
        self.session = self.session.at(f"found:{index}")
        if not self.found_screen(index):
            self.session = self.session.at(None)
            self.effects.append(Render(self.panel_screen()))

    def in_group_screen(self) -> bool:
        """True while the admin is working inside one heading's own screen."""
        return bool((self.session.cursor or "").startswith("group:"))

    def commit_quietly(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Write, then draw the same screen again instead of closing the session."""
        self.session = self.session.with_status(Status.COMMITTING, awaiting="quiet")
        self.effects.append(Commit(kind, payload, quiet=True))

    def commit_and_return(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Write, then draw the panel again with what is saved now."""
        self.session = self.session.with_status(Status.COMMITTING, awaiting="panel")
        self.effects.append(Commit(kind, payload, quiet=True))

    def save(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Write, quietly while a heading's own screen is open."""
        if self.in_group_screen():
            self.commit_quietly(kind, payload)
            return
        self.commit_and_return(kind, payload)

    def listed(self) -> Mapping[str, Any]:
        """The saved document: the panel's pickers list what is saved."""
        return self.document

    def seed_for_edit(self) -> dict[str, Answer]:
        """The saved settings: an edit from the panel starts from them."""
        return document_answers(self.document)

    def stored_items(self) -> list[dict[str, Any]]:
        """The items stored under the list, as the panel shows them."""
        composition = self.definition.composition
        assert composition is not None
        return items_of(self.document, composition.key)

    def item_seed(self, index: int) -> dict[str, Answer]:
        """The answers of the saved item at `index`."""
        items = self.stored_items()
        return item_answers(items[index]) if 0 <= index < len(items) else {}

    def member_index(self, user_id: str) -> int | None:
        """Where the saved item of the member `user_id` sits."""
        composition = self.definition.composition
        assert composition is not None and composition.items.unique_by
        unique = composition.items.unique_by
        return next(
            (
                position
                for position, item in enumerate(self.stored_items())
                if str(unwrap(item.get(unique))) == user_id
            ),
            None,
        )

    def remove_items(self, indexes: Sequence[int]) -> None:
        """Save the list without the items at `indexes`, or turn off what needs them."""
        composition = self.definition.composition
        assert composition is not None
        items = self.stored_items()
        chosen = [index for index in indexes if 0 <= index < len(items)]
        removed = [items[index] for index in chosen]

        emptied = len(items) - len(chosen) < kept_items(composition)
        if chosen and emptied and composition.items.remove_each:
            self.commit("disable", {"items": items})
            return
        self.save("remove_item", {"indexes": chosen, "items": removed})

    def item_index(self, target: str) -> int | None:
        """The position `target` (`key$index`) names among the saved items."""
        _, _, number = target.partition("$")
        if not number.isdigit() or int(number) >= len(self.stored_items()):
            return None
        return int(number)

    def only_item_warning(self, index: int) -> str:
        """What removing the only item says: the item as the panel shows it, and why."""
        composition = self.definition.composition
        assert composition is not None and composition.only_item_warning is not None
        item = titled_items(composition, [self.stored_items()[index]], self.locale)[0]
        heading = item_heading(step_title(composition, self.locale), index)
        block = "\n".join((heading, *item_lines(item, self.locale)))

        feature = without_leading_emoji(self.definition.steps[0].title.get(self.locale))
        warning = composition.only_item_warning.get(self.locale)
        return warning.replace("$item", block).replace("$feature", feature)

    def edit_finished(self, answers: Mapping[str, Answer]) -> None:
        """Save the edited settings."""
        self.commit_and_return("edit", {"answers": answers})

    def item_finished(
        self, event: ev.ChildFinished, item: Mapping[str, Answer]
    ) -> None:
        """Save the item, refusing an addition the saved list already holds."""
        composition = self.definition.composition
        assert composition is not None
        unique = composition.items.unique_by

        if event.child_mode == "add_item" and unique:
            wanted = raw_value(item.get(unique))
            if any(
                str(unwrap(existing.get(unique))) == wanted
                for existing in self.stored_items()
            ):
                self.refuse_change("item-already-registered")
                return
        kind = "add_item" if event.child_mode == "add_item" else "edit_item"
        self.save(kind, {"answers": item, "index": event.index})

    def refuse_change(self, copy: str) -> None:
        """Tell the admin why nothing was saved, then draw the same screen again."""
        self.session = self.session.with_status(Status.ACTIVE)
        self.effects.append(ShowError(copy))
        self.rerender()

    def open_group(self, key: str) -> None:
        """The screen a heading opens, with the rows it holds."""
        screen = group_screen(
            self.definition, self.document, self.locale, key, self.panel
        )
        self.session = self.session.at(f"group:{key}")
        self.effects.append(Render(screen))


def panel_screen(
    definition: FormDefinition,
    document: Mapping[str, Any],
    locale: str,
    panel: PanelExtras,
    page: int = 0,
) -> Screen:
    """The whole manager panel."""
    first = definition.steps[0]
    title = first.title.get(locale)

    if not panel.enabled:
        title += f" ({text('commands.command-events.paused.key', locale)})"
    rows = (
        placed(panel.rows, definition.steps, locale)
        if panel.rows is not None
        else panel_rows(definition, document, locale)
    )
    shown = [row for row in rows if not row.group_footer]
    empty = _empty_lists(definition, document)
    grouped = panel_groups(shown, title, locale, empty, managed=True)
    pages = _pages(definition, document, panel.pages, page, locale)
    footer = _footer(definition, rows, locale, empty)
    reached = [
        _reached_by(definition, button) for group in footer for button in group.actions
    ]
    sections_cover = every_setting_has_a_button(
        definition, document, (*grouped, *pages), locale, rows, reached
    )
    described = (
        first.manager_description
        if isinstance(first, IntroStep) and first.manager_description
        else first.description
    )
    shown_panel = Panel(
        title=title,
        intro=described.get(locale),
        groups=(*grouped, *pages, *footer),
        info=panel.info,
        info_title=panel.info_title,
        info_action=panel.info_action,
        thumbnail=KeikoIcons.IMAGE_01,
        edit_label=label("edit", locale),
        remove_label=label("remove-item", locale),
    )
    inline = _draws_its_list(definition, (*grouped, *pages))

    return Screen(
        components=(shown_panel,),
        buttons=panel_buttons(
            definition, document, locale, panel, sections_cover, inline
        ),
        flavour="components_v2",
        layout_footer=first.footer.get(locale) if first.footer else "",
    )


def _reached_by(definition: FormDefinition, button: Button) -> str:
    if button.action == "search" and definition.composition is not None:
        return definition.composition.key
    return button.action.partition(":")[2]


def _empty_lists(
    definition: FormDefinition, document: Mapping[str, Any]
) -> frozenset[str]:
    composition = definition.composition
    if composition is None or items_of(document, composition.key):
        return frozenset()
    return frozenset({composition.key})


def _draws_its_list(definition: FormDefinition, groups: Sequence[PanelGroup]) -> bool:
    composition = definition.composition
    if composition is None:
        return False
    add = f"add:{composition.key}"
    return any(part.action == add for group in groups for part in group.parts)


def _pages(
    definition: FormDefinition,
    document: Mapping[str, Any],
    pages: Sequence[PanelPage],
    page: int,
    locale: str,
) -> tuple[PanelGroup, ...]:
    if not pages:
        return ()
    index = min(max(page, 0), len(pages) - 1)
    shown = pages[index]
    heading = f"### {shown.title}"
    head = (heading, shown.description) if shown.description else (heading,)
    composition = definition.composition
    actions: tuple[Button, ...] = ()

    if len(pages) > 1:
        last = len(pages) - 1
        actions = (
            Button("", f"page:{max(index - 1, 0)}", emoji="◀️", disabled=index == 0),
            Button(
                "", f"page:{min(index + 1, last)}", emoji="▶️", disabled=index == last
            ),
        )
    if composition is None:
        return (PanelGroup(None, heading, (*head, *shown.lines), actions=actions),)

    count = len(items_of(document, composition.key))
    add = list_adder(composition, count, f"add:{composition.key}", locale)
    part = PanelPart(
        composition.key,
        head,
        add.label,
        add.action,
        add.emoji or "",
        shown.lines,
        actions,
        add.disabled,
        add.description,
    )
    return (PanelGroup(None, heading, (), (part,), keys=(composition.key,)),)


def _footer(
    definition: FormDefinition,
    rows: Sequence[PanelRow],
    locale: str,
    empty: frozenset[str],
) -> tuple[PanelGroup, ...]:
    buttons: list[Button] = []
    composition = definition.composition
    if composition is not None and composition.search and composition.key not in empty:
        buttons.append(
            Button(
                label("search", locale),
                "search",
                emoji="🔎",
                description=caption("search", locale),
            )
        )
    entries: dict[str, Button] = {}
    for row in rows:
        if row.group_footer and row.group and row.group not in entries:
            entries[row.group] = Button(
                row.group_footer, f"edit:group:{row.group}", emoji=row.group_icon
            )
    buttons += entries.values()
    if not buttons:
        return ()
    return (PanelGroup(None, "", (), actions=tuple(buttons)),)


def panel_buttons(
    definition: FormDefinition,
    document: Mapping[str, Any],
    locale: str,
    panel: PanelExtras,
    sections_cover: bool,
    inline: bool = False,
) -> tuple[Button, ...]:
    """Every manager button in the order the panel shows them."""
    buttons: list[Button] = []

    if not sections_cover:
        buttons.append(_button("edit", "edit", "📝", locale))
    if panel.enabled:
        buttons.append(_button("pause", "lifecycle:pause", "⏸️", locale))
    else:
        buttons.append(_button("unpause", "lifecycle:unpause", "▶️", locale))
    buttons.append(_button("disable", "lifecycle:disable", "🚫", locale))
    composition = definition.composition

    if composition is not None and not inline:
        count = len(items_of(document, composition.key))
        if count < composition.items.max:
            buttons.append(_button("add", "add", "➕", locale))
        if count > kept_items(composition) and not composition.search:
            buttons.append(_button("remove", "remove", "🗑️", locale))
    buttons.append(_button("history", "aside:history", "📜", locale))
    buttons.extend(panel.extra_buttons)
    buttons.append(_button("help", "aside:help", "🙋", locale))

    return tuple(buttons)


def _button(key: str, action: str, emoji: str, locale: str) -> Button:
    return Button(
        label(key, locale), action, "secondary", emoji, description=caption(key, locale)
    )


def group_screen(
    definition: FormDefinition,
    document: Mapping[str, Any],
    locale: str,
    key: str,
    panel: PanelExtras | None = None,
) -> Screen:
    """One heading's settings on a screen of their own, with their own buttons."""
    built = panel.rows if panel is not None else None
    rows = [
        replace(row, group_screen=False)
        for row in (
            placed(built, definition.steps, locale)
            if built is not None
            else panel_rows(definition, document, locale)
        )
        if row.group == key
    ]
    title = next((row.group_title for row in rows if row.group_title), key)
    icon = next((row.group_icon for row in rows if row.group_icon), "")
    values = {name: unwrap(value) for name, value in document.items()}
    blocks: list[PanelGroup] = []
    shared: dict[str, int] = {}
    for row in rows:
        owner = _opened_with(definition, row)
        if owner is None:
            blocks += _group_blocks(definition, row, locale)
            continue
        if owner in shared:
            block = blocks[shared[owner]]
            part = block.parts[0]
            line = _group_line(row, locale)
            blocks[shared[owner]] = replace(
                block, parts=(replace(part, lines=(*part.lines, line)),)
            )
            continue
        shared[owner] = len(blocks)
        part = PanelPart(owner, (_group_line(row, locale),))
        blocks.append(PanelGroup(None, "", (), (part,)))
    first = definition.steps[0]
    shown = Panel(
        title=f"{icon} {title}".strip(),
        intro=_group_intro(definition, key, locale, Scope(values)),
        groups=tuple(blocks),
        thumbnail=KeikoIcons.IMAGE_01,
        edit_label=label("edit", locale),
        remove_label=label("remove-item", locale),
    )
    return Screen(
        components=(shown,),
        buttons=(Button(label("back", locale), "picker_back"),),
        flavour="components_v2",
        layout_footer=first.footer.get(locale) if first.footer else "",
    )


def _opened_with(definition: FormDefinition, row: PanelRow) -> str | None:
    if row.style == "composition" or _toggled(definition, row.key):
        return None
    if row.target and "/" not in row.target:
        return row.target
    if row.target:
        return None
    return next(
        (step.key for step in definition.steps if row.key in produced_keys(step)), None
    )


def _toggled(definition: FormDefinition, key: str) -> bool:
    return any(
        section.holds_several() and section.state.value == key
        for step in definition.steps
        if isinstance(step, CardStep)
        for section in step.sections
    )


def _group_line(row: PanelRow, locale: str) -> str:
    value, is_block = row_value(row, locale)
    icon = row.icon or Emojis.FRISBEE_EMOJI
    return f"{icon} {labelled(row.title, value, chr(10) if is_block else ' ')}"


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
    return chosen_text(ref.description, ref.description_when, scope).get(locale)


def _group_blocks(
    definition: FormDefinition, row: PanelRow, locale: str
) -> list[PanelGroup]:
    if row.style == "composition" and row.composition is not None:
        shown = view_constants.GROUP_ITEMS_SHOWN
        return item_groups(row, locale, True, limit=shown, headed=False)
    icon = row.icon or Emojis.FRISBEE_EMOJI
    options = options_for(definition.steps, row.key)
    if options and _toggled(definition, row.key):
        chosen = [str(found) for found in listed(unwrap(row.value))]
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


def found_screen(
    definition: FormDefinition,
    composition: CompositionStep,
    item: Mapping[str, Any],
    index: int,
    locale: str,
    extra: FoundItem | None = None,
    picture: str = "",
) -> Screen:
    """The item a search found under a bare title, its lines, picture, Edit, Remove."""
    target = f"{composition.key}${index}"
    named = composition.items.unique_by
    titled = {
        key: entry
        for key, entry in titled_items(composition, [item], locale)[0].items()
        if key != named
    }
    name = item_name(composition, item, index, locale, styled=True)
    title = (
        composition.found_title.get(locale).replace("$item", name)
        if composition.found_title
        else f"{composition.emoji or ''} {name}".strip()
    )
    extra = extra or FoundItem()
    block = PanelGroup(
        None,
        title,
        (*item_lines(titled, locale), *extra.lines),
        actions=(
            Button(label("edit", locale), f"edit:{target}", "secondary", "✏️"),
            Button(label("remove", locale), f"remove_this:{target}", "danger", "🗑️"),
        ),
        keys=(target,),
        media=picture,
    )
    first = definition.steps[0]
    shown = Panel(title=title, intro="", groups=(block,))
    return Screen(
        components=(shown,),
        buttons=(Button(label("back", locale), "picker_back"),),
        flavour="components_v2",
        layout_footer=first.footer.get(locale) if first.footer else "",
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


def _on_started(manager: Manager) -> None:
    manager.session = manager.session.at(None)
    manager.effects.append(Render(manager.panel_screen()))
    manager.emit("feature.manager_opened", enabled=manager.panel.enabled)


def _on_from_setup(manager: Manager) -> None:
    manager.session = manager.session.at(None).with_change_saved()
    manager.effects.append(Render(manager.panel_screen()))


def _on_drafted(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, ev.Drafted)
    answers = {key: Answer(value) for key, value in event.changes.items()}
    manager.session = manager.session.with_answers(answers)
    manager.effects.append(Ack())


def _on_picker_closed(manager: Manager) -> None:
    cursor = manager.session.cursor or ""
    on_own_screen = cursor.startswith(("group:", "found:"))
    if on_own_screen and manager.session.status is not Status.AWAITING:
        manager.session = manager.session.at(None)
        manager.effects.append(Render(manager.panel_screen()))
        return
    manager.session = manager.session.with_status(Status.ACTIVE)
    manager.rerender()


def _on_item_removal_requested(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, ItemRemovalRequested)
    composition = manager.definition.composition
    index = manager.item_index(event.target)

    if composition is None or index is None:
        manager.redraw()
        return
    each = composition.items.remove_each
    only = each and len(manager.stored_items()) <= kept_items(composition)
    confirm_removal(manager, (index,), manager.only_item_warning(index) if only else "")


def _on_page_turned(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, PageTurned)
    manager.session = manager.session.at(f"page:{max(event.index, 0)}")
    manager.effects.append(Render(manager.panel_screen()))


def _on_search_requested(manager: Manager) -> None:
    composition = manager.definition.composition
    if composition is None or not composition.search:
        manager.redraw()
        return
    open_member_picker(manager, "find")


def _on_option_toggled(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, OptionToggled)
    manager.commit_quietly(
        "toggle",
        {"key": event.target, "value": event.value, "turned_on": event.turned_on},
    )


def _on_lifecycle(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, Lifecycle)
    manager.session = manager.session.with_status(
        Status.AWAITING, awaiting=event.action
    )
    manager.effects.append(
        OpenModal(
            confirmation_modal(event.action, manager.locale),
            "word",
            event.action,
        )
    )


def _on_lifecycle_confirmed(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, LifecycleConfirmed)
    expected = confirmation_word(event.action, manager.locale)
    if event.word.strip().lower() != expected.lower():
        manager.session = manager.session.with_status(Status.ACTIVE)
        manager.effects.append(Ack())
        return
    manager.commit(event.action, {})


def _on_commit_succeeded(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, ev.CommitSucceeded)
    if manager.session.awaiting in ("quiet", "panel"):
        manager.session = manager.session.with_status(Status.ACTIVE)
        manager.session = manager.session.with_change_saved()
        if (manager.session.cursor or "").startswith("found:"):
            manager.session = manager.session.at(None)
        manager.rerender()
        return
    on_saved(manager, event.kind)


def _on_commit_failed(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, ev.CommitFailed)
    refused = event.error in ("stale", "duplicate")

    if refused and not manager.document:
        ended = Status.COMPLETED if manager.session.changes_saved else Status.CANCELLED
        manager.session = manager.session.with_status(ended)
        manager.effects.append(Finalize("closed"))
    elif event.error == "stale":
        manager.session = manager.session.with_status(Status.ACTIVE)
        manager.effects.append(Notice("stale"))
        manager.rerender()
    elif event.error == "duplicate" or manager.session.awaiting == "quiet":
        copy = "item-already-registered" if refused else "command-generic-error"
        manager.refuse_change(copy)
    else:
        on_commit_failed(manager)


HANDLERS: dict[type[ev.Event], Callable[[Manager], None]] = {
    ev.Started: _on_started,
    FromSetup: _on_from_setup,
    ev.Answered: on_member_confirmed,
    ev.Drafted: _on_drafted,
    ev.PickerClosed: _on_picker_closed,
    ev.KeepEditing: on_keep_editing,
    ev.EditRequested: on_edit_requested,
    ev.AddRequested: on_add_requested,
    ev.RemoveRequested: on_remove_requested,
    ItemRemovalRequested: _on_item_removal_requested,
    OptionToggled: _on_option_toggled,
    ev.TargetChosen: on_target_chosen,
    ev.TargetsChosen: on_targets_chosen,
    ev.RemovalConfirmed: on_removal_confirmed,
    ev.MemberChosen: on_member_chosen,
    ev.ItemRemoved: on_item_removed,
    ev.ChildFinished: on_child_finished,
    ev.ScreenRequested: on_screen_requested,
    PageTurned: _on_page_turned,
    SearchRequested: _on_search_requested,
    Lifecycle: _on_lifecycle,
    LifecycleConfirmed: _on_lifecycle_confirmed,
    ev.Aside: on_aside,
    ev.CommitSucceeded: _on_commit_succeeded,
    ev.CommitFailed: _on_commit_failed,
    ev.Expired: on_expired,
}


def decide(
    definition: FormDefinition,
    session: FormSession,
    event: ev.Event,
    context: Context | None = None,
    panel: PanelExtras | None = None,
) -> Decision:
    """The manage session after `event`, and what must happen next."""
    manager = Manager(
        definition, session, event, context or Context(), panel or PanelExtras()
    )
    return settle(manager, HANDLERS)

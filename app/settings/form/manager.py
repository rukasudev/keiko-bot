"""The manager: the panel over a saved document, its screens and what its buttons do.

It decides on its own, over the sessions the panel opens; the form never
names it. Its Edit, Add and Remove open the form as a child session, and
what the child answers comes back here to be saved.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
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
from app.settings.form.conditions import Scope, evaluate
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
    on_remove_requested,
    on_saved,
    on_screen_requested,
    on_target_chosen,
    settle,
)
from app.settings.form.form_state import Answer, FormSession, Status, raw_value
from app.settings.form.form_yaml import (
    CardStep,
    FormDefinition,
    IntroStep,
    PanelGroupRef,
    options_for,
)
from app.settings.form.responses.responses import (
    document_answers,
    item_answers,
    items_of,
)
from app.settings.form.responses.summary import (
    PanelRow,
    every_setting_has_a_button,
    item_lines,
    labelled,
    listed,
    panel_groups,
    panel_rows,
    placed,
    row_value,
    unwrap,
)

LIFECYCLE_EVENTS = {"pause": "paused", "unpause": "unpaused", "disable": "disabled"}


@dataclass(frozen=True)
class PanelExtras:
    """What a feature adds to its panel: rows, a closing note, buttons, on or off."""

    rows: Sequence[PanelRow] | None = None
    info: str = ""
    info_title: str = ""
    extra_buttons: Sequence[Button] = ()
    enabled: bool = True


@dataclass(frozen=True)
class RemoveItemConfirmed(ev.Event):
    """The admin confirmed removing the item the Remove beside it named."""

    target: str = ""


@dataclass(frozen=True)
class OptionToggled(ev.Event):
    """One option of a multiple choice was turned on or off, on the screen."""

    target: str = ""
    value: str = ""
    turned_on: bool = False


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
        """Draw the panel again, or the heading's own screen the admin is on."""
        cursor = self.session.cursor or ""
        if cursor.startswith("group:"):
            key = cursor.split(":", 1)[1]
            screen = group_screen(self.definition, self.document, self.locale, key)
            self.effects.append(Render(screen))
            return
        self.effects.append(Render(self.panel_screen()))

    def panel_screen(self) -> Screen:
        """The whole panel over the saved document."""
        return panel_screen(self.definition, self.document, self.locale, self.panel)

    def in_group_screen(self) -> bool:
        """True while the admin is working inside one heading's own screen."""
        return bool((self.session.cursor or "").startswith("group:"))

    def commit_quietly(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Write, then draw the same screen again instead of closing the session."""
        self.session = self.session.with_status(Status.COMMITTING, awaiting="quiet")
        self.effects.append(Commit(kind, payload, quiet=True))

    def save(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Write, quietly while a heading's own screen is open."""
        if self.in_group_screen():
            self.commit_quietly(kind, payload)
            return
        self.commit(kind, payload)

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

    def remove_item(self, index: int) -> None:
        """Save the list without the item at `index`."""
        items = self.stored_items()
        removed = items[index] if 0 <= index < len(items) else {}
        self.save("remove_item", {"index": index, "item": removed})

    def edit_finished(self, answers: Mapping[str, Answer]) -> None:
        """Save the edited settings."""
        self.commit("edit", {"answers": answers})

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
                self.effects.append(
                    ShowError("item-already-registered", delete_after=None)
                )
                self.effects.append(Finalize("duplicate"))
                self.session = self.session.with_status(Status.COMPLETED)

                return
        kind = "add_item" if event.child_mode == "add_item" else "edit_item"
        self.save(kind, {"answers": item, "index": event.index})

    def open_group(self, key: str) -> None:
        """The screen a heading opens, with the rows it holds."""
        screen = group_screen(self.definition, self.document, self.locale, key)
        self.session = self.session.at(f"group:{key}")
        self.effects.append(Render(screen))


def panel_screen(
    definition: FormDefinition,
    document: Mapping[str, Any],
    locale: str,
    panel: PanelExtras,
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
    grouped = panel_groups(rows, title, locale)
    sections_cover = every_setting_has_a_button(
        definition, document, grouped, locale, rows
    )
    described = (
        first.manager_description
        if isinstance(first, IntroStep) and first.manager_description
        else first.description
    )
    shown = Panel(
        title=title,
        intro=described.get(locale),
        groups=grouped,
        info=panel.info,
        info_title=panel.info_title,
        thumbnail=KeikoIcons.IMAGE_01,
        edit_label=label("edit", locale),
        remove_label=label("remove-item", locale),
    )

    return Screen(
        components=(shown,),
        buttons=panel_buttons(definition, document, locale, panel, sections_cover),
        flavour="components_v2",
        layout_footer=first.footer.get(locale) if first.footer else "",
    )


def panel_buttons(
    definition: FormDefinition,
    document: Mapping[str, Any],
    locale: str,
    panel: PanelExtras,
    sections_cover: bool,
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

    if composition is not None:
        stored = document.get(composition.key)
        count = len(stored.get("values") or []) if isinstance(stored, Mapping) else 0

        if count < composition.items.max:
            buttons.append(_button("add", "add", "➕", locale))
        if count > 1:
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
) -> Screen:
    """One heading's settings on a screen of their own, with their own buttons.

    A list becomes one block per item, each with Edit and Remove; a setting
    with declared options becomes the options themselves, as buttons that turn
    on and off where they are read.
    """
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
    first = definition.steps[0]
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
                tuple(item_lines(item, locale)),
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


def _on_drafted(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, ev.Drafted)
    answers = {key: Answer(value) for key, value in event.changes.items()}
    manager.session = manager.session.with_answers(answers)
    manager.effects.append(Ack())


def _on_picker_closed(manager: Manager) -> None:
    if manager.in_group_screen():
        manager.session = manager.session.at(None).with_status(Status.ACTIVE)
        manager.effects.append(Render(manager.panel_screen()))
        return
    manager.session = manager.session.with_status(Status.ACTIVE)
    manager.rerender()


def _on_remove_item_confirmed(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, RemoveItemConfirmed)
    manager.session = manager.session.with_status(Status.ACTIVE)
    _, _, number = event.target.partition("$")

    if not number.isdigit():
        manager.rerender()
        return
    manager.remove_item(int(number))


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
    if manager.session.awaiting == "quiet":
        manager.session = manager.session.with_status(Status.ACTIVE)
        manager.rerender()
        return
    on_saved(manager, event.kind)


def _on_commit_failed(manager: Manager) -> None:
    event = manager.event
    assert isinstance(event, ev.CommitFailed)
    refused = event.error in ("stale", "duplicate")

    if refused and not manager.document:
        manager.session = manager.session.with_status(Status.CANCELLED)
        manager.effects.append(Finalize("closed"))
    elif event.error == "stale":
        manager.session = manager.session.with_status(Status.ACTIVE)
        manager.effects.append(Notice("stale"))
        manager.rerender()
    elif manager.session.awaiting == "quiet":
        copy = "item-already-registered" if refused else "command-generic-error"
        manager.session = manager.session.with_status(Status.ACTIVE)
        manager.effects.append(ShowError(copy))
        manager.rerender()
    else:
        on_commit_failed(manager)


HANDLERS: dict[type[ev.Event], Callable[[Manager], None]] = {
    ev.Started: _on_started,
    ev.Answered: on_member_confirmed,
    ev.Drafted: _on_drafted,
    ev.PickerClosed: _on_picker_closed,
    ev.KeepEditing: on_keep_editing,
    ev.EditRequested: on_edit_requested,
    ev.AddRequested: on_add_requested,
    ev.RemoveRequested: on_remove_requested,
    RemoveItemConfirmed: _on_remove_item_confirmed,
    OptionToggled: _on_option_toggled,
    ev.TargetChosen: on_target_chosen,
    ev.MemberChosen: on_member_chosen,
    ev.ItemRemoved: on_item_removed,
    ev.ChildFinished: on_child_finished,
    ev.ScreenRequested: on_screen_requested,
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

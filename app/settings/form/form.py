"""`decide(definition, session, event, context) -> Decision`: the form engine.

Pure: no I/O, no Discord, no clock of its own. Every guard the hostile
environment needs lives here first: a seen event is a no-op, a click from an
older screen is stale, a closed session answers nothing but a notice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from app.constants import KeikoIcons, Style
from app.settings.form import events as ev
from app.settings.form.actions import Refusal, configuration_card, registry, selects
from app.settings.form.actions.action import Context, confirm_button, label
from app.settings.form.components import (
    Button,
    ChoiceOption,
    OptionSelect,
    Picker,
    Screen,
)
from app.settings.form.conditions import Scope, evaluate, explain
from app.settings.form.copy import text
from app.settings.form.effects import (
    Ack,
    Commit,
    Confirm,
    Dismiss,
    Effect,
    Finalize,
    Notice,
    OpenChild,
    OpenModal,
    Render,
    ResumeChild,
    ResumeParent,
    RunAside,
    ShowError,
)
from app.settings.form.form_state import (
    AddItem,
    Answer,
    Edit,
    EditItem,
    FormSession,
    Mode,
    Status,
    new_session,
    raw_value,
)
from app.settings.form.form_yaml import (
    CardStep,
    CompositionStep,
    FormDefinition,
    MultiPickStep,
    Step,
    produced_keys,
)
from app.settings.form.responses.responses import document_values, to_document
from app.settings.form.responses.summary import (
    configured_steps,
    edit_options,
    part_targets,
    remove_options,
    uses_member_picker,
)

FINAL_KIND = {
    "setup": "enabled",
    "edit": "edited",
    "edit_item": "edited",
    "add_item": "added",
    "remove_item": "removed",
    "pause": "paused",
    "unpause": "unpaused",
    "disable": "disabled",
}
NOT_ON_BACK = ("text",)


@dataclass(frozen=True)
class RuleTrace:
    """One rule the decision evaluated, and how it went."""

    step_key: str
    explanation: str
    skipped: bool


@dataclass(frozen=True)
class Decision:
    """The session after the event and everything that must happen next."""

    session: FormSession
    effects: tuple[Effect, ...] = ()
    evaluated_rules: tuple[RuleTrace, ...] = ()
    analytics: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    rejected: str | None = None


def steps_for(definition: FormDefinition, mode: Mode) -> tuple[Step, ...]:
    """The steps a session in `mode` walks."""
    composition = definition.composition
    if isinstance(mode, (AddItem, EditItem)):
        return composition.steps if composition else ()
    if isinstance(mode, Edit):
        wanted = set(mode.keys)
        if mode.part is not None:
            return tuple(step for step in definition.steps if step.key in wanted)
        chosen = [
            step
            for step in definition.steps
            if step.key in wanted or _depends_on(step, wanted)
        ]
        return tuple(chosen)
    return definition.steps


def _depends_on(step: Step, keys: set[str]) -> bool:
    from app.settings.form.form_yaml import _leaves

    return any(leaf.key in keys for leaf in _leaves(step.when))


class Engine(ABC):
    """A decision under construction: the session as it changes, what must happen."""

    def __init__(
        self,
        definition: FormDefinition,
        session: FormSession,
        event: ev.Event,
        context: Context,
    ) -> None:
        self.definition = definition
        self.session = session
        self.event = event
        self.context = replace(
            context, definition=definition, locale=session.origin.locale
        )
        self.effects: list[Effect] = []
        self.rules: list[RuleTrace] = []
        self.analytics: list[tuple[str, Mapping[str, Any]]] = []

    @property
    def locale(self) -> str:
        """The locale of the session."""
        return self.session.origin.locale

    def emit(self, name: str, **props: Any) -> None:
        """Record a product event for the adapter to emit."""
        self.analytics.append((name, props))

    def open_child(
        self,
        mode: Mode,
        answers: Mapping[str, Answer],
        cursor: str | None = None,
    ) -> None:
        """Open a child session under this one and wait for it."""
        child = new_session(
            self.session.definition,
            mode,
            self.session.origin,
            ttl_seconds=self.context.ttl_seconds,
            parent_id=self.session.id,
            answers=answers,
            now=self.context.now,
        )
        self.session = self.session.with_status(Status.AWAITING, awaiting="child")

        if cursor is not None:
            self.session = replace(self.session, cursor=cursor)
        self.effects.append(OpenChild(child))

    def commit(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Ask the feature to write; the session waits for the outcome."""
        self.session = self.session.with_status(Status.COMMITTING)
        self.effects.append(Commit(kind, payload))

    def decided(self) -> Decision:
        """The decision built so far, with the session's bookkeeping done."""
        session = self.session.remember(self.event.event_id)
        if not session.is_closed:
            session = session.touched(self.context.now, self.context.ttl_seconds)
        if any(isinstance(effect, Render) for effect in self.effects):
            session = session.rendered()

        return Decision(
            session,
            tuple(self.effects),
            tuple(self.rules),
            tuple(self.analytics),
        )

    @abstractmethod
    def rerender(self) -> None:
        """Draw what the session shows again, without counting a step view."""

    @abstractmethod
    def listed(self) -> Mapping[str, Any]:
        """The document the edit and remove pickers list."""

    @abstractmethod
    def seed_for_edit(self) -> dict[str, Answer]:
        """The answers an edit of one step starts from."""

    @abstractmethod
    def item_seed(self, index: int) -> dict[str, Answer]:
        """The answers of the item at `index`, empty when there is none."""

    @abstractmethod
    def member_index(self, user_id: str) -> int | None:
        """Where the item of the member `user_id` sits in the list."""

    @abstractmethod
    def remove_item(self, index: int) -> None:
        """Take the item at `index` out of the list."""

    @abstractmethod
    def edit_finished(self, answers: Mapping[str, Answer]) -> None:
        """An edit of one step came back with its answers."""

    @abstractmethod
    def item_finished(
        self, event: ev.ChildFinished, item: Mapping[str, Answer]
    ) -> None:
        """An item came back from its child session."""

    @abstractmethod
    def open_group(self, key: str) -> None:
        """A heading's Edit was pressed."""


class Form(Engine):
    """A form session's decision: navigation, steps, cards and the review's lists."""

    def __init__(
        self,
        definition: FormDefinition,
        session: FormSession,
        event: ev.Event,
        context: Context,
    ) -> None:
        super().__init__(definition, session, event, context)
        self.steps = steps_for(definition, session.mode)
        self.context = replace(self.context, steps=self.steps)

    def scope(self) -> Scope:
        """The values rules see: the document, then the answers, then the parent."""
        values = {**document_values(self.context.document), **self.session.values()}
        parent = (
            Scope(self.context.parent_values) if self.context.parent_values else None
        )
        composition = self.definition.composition
        count = None

        if composition is not None and composition.key in values:
            stored = values[composition.key]
            count = len(stored) if isinstance(stored, (list, tuple)) else None
        return Scope(values, parent=parent, items_count=count)

    def step_context(self, can_go_back: bool = False) -> Context:
        """What a step reads while it renders or parses."""
        mode = self.session.mode
        return replace(
            self.context,
            scope=self.scope(),
            can_go_back=can_go_back,
            item_index=mode.index if isinstance(mode, EditItem) else None,
        )

    def step(self, key: str | None) -> Step | None:
        """The step called `key` among the steps in play."""
        if key is None:
            return None
        return next((step for step in self.steps if step.key == key), None)

    def index(self, key: str | None) -> int:
        """The position of `key` among the steps in play, -1 when absent."""
        return next(
            (index for index, step in enumerate(self.steps) if step.key == key), -1
        )

    def allowed(self, step: Step) -> bool:
        """True when the step's rule holds now, recording the evaluation."""
        scope = self.scope()
        outcome = evaluate(step.when, scope)
        if step.when is not None:
            self.rules.append(
                RuleTrace(step.key, explain(step.when, scope), not outcome)
            )
        return outcome

    def next_key(self, after: int) -> str | None:
        """The first allowed step after position `after`."""
        for index in range(after + 1, len(self.steps)):
            if self.allowed(self.steps[index]):
                return self.steps[index].key
        return None

    def previous_key(self, before: int) -> str | None:
        """The step Back lands on from position `before`, None at the start.

        A screen wins over a modal: Back skips the modal steps when a screen
        comes before them, and reopens the nearest modal when nothing else does.
        """
        modal: str | None = None
        for index in range(before - 1, -1, -1):
            step = self.steps[index]
            if step.kind == "intro":
                break
            if not self.allowed(step):
                continue
            if step.kind in NOT_ON_BACK:
                modal = modal or step.key
                continue
            return step.key
        return modal

    def can_go_back(self, key: str | None) -> bool:
        """True when Back has somewhere to go from `key`."""
        return self.previous_key(self.index(key)) is not None

    def show(self, key: str) -> None:
        """Render the step at `key` on the current message, or open its modal."""
        step = self.step(key)
        assert step is not None
        if isinstance(step, CompositionStep):
            self.open_child(AddItem(), answers={}, cursor=key)
            return
        if step.kind == "review":
            self.ensure_composition_answer()
        screen = registry()[step.kind].render(
            step, self.session, self.step_context(self.can_go_back(key))
        )
        self.session = self.session.at(key)

        if screen.flavour == "modal":
            self.effects.append(OpenModal(screen, "modal", key))
        else:
            self.effects.append(Render(screen))
        self.emit(
            "setup.step_viewed",
            step_key=key,
            step_action=step.kind,
            step_index=self.index(key),
        )

    def refuse(self, refusal: Refusal, step: Step) -> None:
        """Answer a refused payload with its error and the matching event."""
        self.effects.append(
            ShowError(
                refusal.error_key,
                refusal.args or {},
                refusal.plain,
                refusal.delete_after,
            )
        )
        if isinstance(step, CardStep):
            self.emit("setup.required_missing", card_key=step.key)
        else:
            self.emit(
                "setup.validation_failed",
                step_key=step.key,
                validation=refusal.error_key,
                error_key=refusal.error_key,
            )

    def complete(self) -> None:
        """The last step was answered: hand over to the parent, or commit."""
        mode = self.session.mode
        parent_id = self.session.parent_id
        answers = self.edited_answers()
        if parent_id is not None:
            self.session = self.session.with_status(Status.COMPLETED)
            index = mode.index if isinstance(mode, EditItem) else None
            self.effects.append(ResumeParent(parent_id, mode.kind, answers, index))
            return
        self.commit(mode.kind, {"answers": answers})

    def edited_answers(self) -> Mapping[str, Answer]:
        """Every answer, or only the edited steps' answers in an edit session."""
        mode = self.session.mode
        if not isinstance(mode, Edit):
            return self.session.answers
        edited = {
            key
            for step in self.steps
            if step.key in mode.keys
            for key in produced_keys(step)
        }
        return {
            key: answer for key, answer in self.session.answers.items() if key in edited
        }

    def advance(self, from_index: int) -> None:
        """Show the next allowed step, or complete when there is none."""
        nxt = self.next_key(from_index)
        if nxt is None:
            self.complete()
            return
        self.show(nxt)

    def review_key(self) -> str | None:
        """The key of the review step, when the steps in play have one."""
        for step in self.steps:
            if step.kind == "review":
                return step.key
        return None

    def rerender(self) -> None:
        """Draw the current step again, without counting a step view."""
        key = self.session.cursor
        step = self.step(key)
        if step is None or isinstance(step, CompositionStep):
            return
        screen = registry()[step.kind].render(
            step, self.session, self.step_context(self.can_go_back(key))
        )
        if screen.flavour == "modal":
            self.effects.append(OpenModal(screen, "modal", step.key))
        else:
            self.effects.append(Render(screen))

    def ensure_composition_answer(self) -> None:
        """A form with a composition always lists it, even with no item yet."""
        composition = self.definition.composition
        if composition is not None and composition.key not in self.session.answers:
            self.session = self.session.with_answer(composition.key, Answer(()))

    def composition_items(self) -> tuple[Mapping[str, Answer], ...]:
        """The items answered under the composition, possibly none."""
        composition = self.definition.composition
        if composition is None:
            return ()
        answer = self.session.answers.get(composition.key)
        return tuple(answer.raw) if answer and answer.raw else ()

    def set_items(self, items: Sequence[Mapping[str, Answer]]) -> None:
        """Replace the composition items."""
        composition = self.definition.composition
        assert composition is not None
        self.session = self.session.with_answer(composition.key, Answer(tuple(items)))

    def merge_item(
        self, item: Mapping[str, Answer], index: int | None
    ) -> tuple[Mapping[str, Answer], ...]:
        """The items with `item` upserted by its unique key or at `index`."""
        composition = self.definition.composition
        assert composition is not None
        items = list(self.composition_items())
        unique = composition.items.unique_by
        same = None

        if unique:
            wanted = raw_value(item.get(unique))
            same = next(
                (
                    position
                    for position, existing in enumerate(items)
                    if wanted is not None
                    and raw_value(existing.get(unique)) == wanted
                    and position != index
                ),
                None,
            )
        target = same if same is not None else index
        if target is None or not 0 <= target < len(items):
            items.append(item)
        else:
            items[target] = item
            if same is not None and index is not None and index != same:
                del items[index]
        return tuple(items)

    def listed(self) -> Mapping[str, Any]:
        """The answers as the document they would save, for the review's pickers."""
        return to_document(self.steps, self.session.answers, self.locale)

    def seed_for_edit(self) -> dict[str, Answer]:
        """The answers so far: an edit from the review starts from them."""
        return dict(self.session.answers)

    def item_seed(self, index: int) -> dict[str, Answer]:
        """The answers of the item at `index` of the list answered so far."""
        answered = self.composition_items()
        return dict(answered[index]) if 0 <= index < len(answered) else {}

    def member_index(self, user_id: str) -> int | None:
        """Where the answered item of the member `user_id` sits."""
        composition = self.definition.composition
        assert composition is not None and composition.items.unique_by
        unique = composition.items.unique_by
        return next(
            (
                position
                for position, item in enumerate(self.composition_items())
                if raw_value(item.get(unique)) == user_id
            ),
            None,
        )

    def remove_item(self, index: int) -> None:
        """Take the item out of the answers and show the same step again."""
        remaining = list(self.composition_items())
        if 0 <= index < len(remaining):
            del remaining[index]

        self.set_items(remaining)
        self.session = self.session.with_status(Status.ACTIVE)
        self.show(self.session.cursor or "")

    def edit_finished(self, answers: Mapping[str, Answer]) -> None:
        """The edited answers replace the old ones, and the review shows again."""
        self.session = self.session.with_answers(answers)
        self.show(self.review_key() or self.session.cursor or "")

    def item_finished(
        self, event: ev.ChildFinished, item: Mapping[str, Answer]
    ) -> None:
        """The item joins the list, then the form moves on or shows the review."""
        self.set_items(self.merge_item(item, event.index))
        cursor = self.session.cursor
        step = self.step(cursor)

        if isinstance(step, CompositionStep) and event.child_mode == "add_item":
            self.advance(self.index(cursor))
            return
        self.show(self.review_key() or cursor or "")

    def open_group(self, _key: str) -> None:
        """A heading's own screen saves as it goes: a setup never offers it."""
        self.effects.append(Notice("stale"))
        self.rerender()


def panel_embed(definition: FormDefinition, locale: str) -> Screen:
    """The embed a picker of the review or of the panel starts from."""
    first = definition.steps[0]
    return Screen(
        title=first.title.get(locale),
        description=first.description.get(locale),
        footer=first.footer.get(locale) if first.footer else "",
        thumbnail=KeikoIcons.IMAGE_01,
        color=Style.BACKGROUND_COLOR,
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


def on_screen_requested(engine: Engine) -> None:
    """Show the current screen again: a modal was dismissed, a parent clicked."""
    engine.rerender()


def on_edit_requested(engine: Engine) -> None:
    """Edit: open a heading, a part, an item or a step, or ask which one."""
    event = engine.event
    assert isinstance(event, ev.EditRequested)
    target = event.target or ""
    composition = engine.definition.composition
    items_target = composition is not None and target == composition.key

    if target.startswith("group:"):
        engine.open_group(target.split(":", 1)[1])
        return
    if "/" in target:
        _open_part_edit(engine, target)
        return
    if "$" in target:
        _open_item(engine, int(target.split("$", 1)[1]))
        return
    if target and not items_target:
        engine.open_child(Edit((target,)), engine.seed_for_edit())
        return
    if items_target and uses_member_picker(composition):
        _open_member_picker(engine, "edit")
        return

    options = edit_options(engine.definition, engine.listed(), engine.locale)
    if items_target:
        options = tuple(
            option for option in options if option.value.startswith(f"{target}$")
        )
    _pick(engine, options, "edit", "edited", unique=composition is not None)


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
        engine.open_child(Edit((step_key,), part=part), engine.seed_for_edit())
        return
    engine.effects.append(Notice("stale"))
    engine.rerender()


def _open_item(engine: Engine, index: int) -> None:
    seed = engine.item_seed(index)
    if not seed:
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return
    engine.open_child(EditItem(index), seed)


def _open_member_picker(engine: Engine, awaiting: str) -> None:
    action = "edited" if awaiting == "edit" else "removed"
    engine.session = engine.session.with_status(
        Status.AWAITING, awaiting=f"member:{awaiting}"
    )
    engine.effects.append(
        Render(member_picker_screen(engine.definition, action, engine.locale))
    )


def _pick(
    engine: Engine,
    options: tuple[ChoiceOption, ...],
    awaiting: str,
    action: str,
    unique: bool,
) -> None:
    if not options:
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return

    base = panel_embed(engine.definition, engine.locale)
    placeholder = text(f"commands.command-events.{action}.placeholder", engine.locale)
    engine.session = engine.session.with_status(Status.AWAITING, awaiting=awaiting)
    engine.effects.append(
        Render(picker_screen(base, placeholder, options, unique, engine.locale))
    )


def on_add_requested(engine: Engine) -> None:
    """Add: open the form again, empty, for one more item."""
    engine.open_child(AddItem(), {})


def on_remove_requested(engine: Engine) -> None:
    """Remove: confirm the item named beside it, or ask which one goes."""
    event = engine.event
    assert isinstance(event, ev.RemoveRequested)

    if event.target:
        engine.session = engine.session.with_status(
            Status.AWAITING, awaiting="remove_item"
        )
        engine.effects.append(Confirm(remove_item_screen(engine.locale, event.target)))
        return
    options = remove_options(engine.definition, engine.listed(), engine.locale)
    _pick(engine, options, "remove", "removed", unique=True)


def on_target_chosen(engine: Engine) -> None:
    """A picker answered: act on the item or the step it names."""
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
        index = int(value.split("$", 1)[1])
        if awaiting == "remove":
            engine.remove_item(index)
        else:
            engine.open_child(EditItem(index), engine.item_seed(index))
        return
    engine.open_child(Edit(tuple(value.split(","))), engine.seed_for_edit())


def on_member_confirmed(engine: Engine) -> None:
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


def on_member_chosen(engine: Engine) -> None:
    """A member picker answered: edit or remove that member's item."""
    event = engine.event
    assert isinstance(event, ev.MemberChosen)
    _choose_member(engine, event.user_id)


def _choose_member(engine: Engine, user_id: str) -> None:
    awaiting = engine.session.awaiting or ""
    action = "edited" if awaiting.endswith("edit") else "removed"
    index = engine.member_index(user_id)

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
        engine.remove_item(index)
        return
    engine.open_child(EditItem(index), engine.item_seed(index))


def on_item_removed(engine: Engine) -> None:
    """An item was confirmed for removal: take it out."""
    event = engine.event
    assert isinstance(event, ev.ItemRemoved)
    engine.remove_item(event.index)


def on_child_finished(engine: Engine) -> None:
    """A child form ended: fold what it answered back into this session."""
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
        engine.edit_finished(answers)
        return
    engine.item_finished(event, answers)


def on_keep_editing(engine: Engine) -> None:
    """The admin kept what a confirmation would have thrown away."""
    engine.session = engine.session.with_status(Status.ACTIVE)
    engine.effects.append(Dismiss())
    engine.emit("setup.discard_recovered", step_key=engine.session.cursor)


def on_aside(engine: Engine) -> None:
    """A read-only button: help, history, preview or a feature's own."""
    event = engine.event
    assert isinstance(event, ev.Aside)
    engine.effects.append(RunAside(event.name))


def on_saved(engine: Engine, kind: str) -> None:
    """The feature wrote what `kind` asked: the session completes and says so."""
    engine.session = engine.session.with_status(Status.COMPLETED)
    engine.effects.append(Finalize(FINAL_KIND.get(kind, kind)))


def on_commit_failed(engine: Engine) -> None:
    """The feature raised: the session fails and the admin reads why."""
    event = engine.event
    assert isinstance(event, ev.CommitFailed)
    engine.session = engine.session.with_status(Status.FAILED)
    kind = "duplicate" if event.error == "duplicate" else "error"
    engine.effects.append(Finalize(kind))
    engine.emit(
        "feature.commit_failed",
        commit_kind=event.kind,
        error_type=event.error,
        step_key=engine.session.cursor,
    )


def on_expired(engine: Engine) -> None:
    """Nobody clicked before the deadline: the session closes as abandoned."""
    engine.emit("setup.abandoned", step_key=engine.session.cursor)
    engine.session = engine.session.with_status(Status.EXPIRED)
    engine.effects.append(Finalize("expired"))


def _on_started(form: Form) -> None:
    session = form.session
    if session.parent_id is None and session.mode.kind == "setup":
        form.emit("feature.setup_opened")
    if isinstance(session.mode, Edit) and session.mode.part is not None:
        _open_part(form, session.mode)
        return
    form.advance(-1)


def _open_part(form: Form, mode: Edit) -> None:
    step = form.step(mode.keys[0])
    if step is None:
        form.advance(-1)
        return
    form.session = form.session.at(step.key)
    if isinstance(step, CardStep):
        index = next(
            (
                position
                for position, section in enumerate(step.sections)
                if section.key == mode.part
            ),
            None,
        )
        if index is None:
            form.rerender()
            return
        _open_section(form, step, index)
        return
    if isinstance(step, MultiPickStep):
        screen = selects.part_screen(
            step, mode.part or "", form.session, form.step_context()
        )
        form.effects.append(Render(screen))
        return
    form.rerender()


def _is_part_edit(form: Form) -> bool:
    mode = form.session.mode
    return isinstance(mode, Edit) and mode.part is not None


def _finish_part(form: Form, step: Step) -> None:
    context = form.step_context()
    parsed = registry()[step.kind].parse(step, None, form.session, context)
    form.session = form.session.with_status(Status.ACTIVE)
    if isinstance(parsed, Refusal):
        form.refuse(parsed, step)
        form.rerender()
        return
    form.session = form.session.with_answers(parsed)
    form.complete()


def _redraw_or_finish(form: Form, step: Step) -> None:
    if _is_part_edit(form):
        _finish_part(form, step)
        return
    form.rerender()


def _on_answered(form: Form) -> None:
    event = form.event
    assert isinstance(event, ev.Answered)
    if event.step_key.startswith("section:"):
        _on_section_changed(form)
        return
    if (form.session.awaiting or "").startswith("member:"):
        on_member_confirmed(form)
        return
    step = form.step(form.session.cursor)
    if step is not None and step.kind in NOT_ON_BACK and step.key != event.step_key:
        form.rerender()
        return
    if step is None or step.key != event.step_key:
        form.effects.append(Notice("stale"))
        form.rerender()
        return
    if step.kind == "review":
        _on_review_confirmed(form)
        return
    context = form.step_context()
    parsed = registry()[step.kind].parse(step, event.payload, form.session, context)

    if isinstance(parsed, Refusal):
        form.refuse(parsed, step)
        return
    form.session = form.session.with_answers(parsed)
    form.emit("setup.step_completed", step_key=step.key, step_action=step.kind)
    form.advance(form.index(step.key))


def _on_drafted(form: Form) -> None:
    event = form.event
    assert isinstance(event, ev.Drafted)
    step = form.step(form.session.cursor)
    if step is None or step.key != event.step_key:
        form.effects.append(Notice("stale"))
        form.rerender()
        return
    if isinstance(step, CardStep):
        context = form.step_context()
        changes = configuration_card.apply_drafts(
            step, event.changes, form.session, context
        )

        if isinstance(changes, Refusal):
            form.refuse(changes, step)
            return
        answer = configuration_card.draft(step, changes, form.session, context)
        form.session = form.session.with_answer(step.key, answer)
        _redraw_or_finish(form, step)

        return
    answers = {key: Answer(value) for key, value in event.changes.items()}
    form.session = form.session.with_answers(answers)

    if isinstance(step, MultiPickStep) and _is_part_edit(form):
        _finish_part(form, step)
        return
    if step.kind == "single_choice":
        form.rerender()
        return
    form.effects.append(Ack())


def _on_section_opened(form: Form) -> None:
    event = form.event
    assert isinstance(event, ev.SectionOpened)
    step = form.step(form.session.cursor)
    if not isinstance(step, CardStep) or step.key != event.step_key:
        form.effects.append(Notice("stale"))
        form.rerender()
        return
    _open_section(form, step, event.index)


def _open_section(form: Form, step: CardStep, index: int) -> None:
    screen, changes = configuration_card.open_section(
        step, index, form.session, form.step_context()
    )
    if changes is not None:
        answer = configuration_card.draft(
            step, changes, form.session, form.step_context()
        )
        form.session = form.session.with_answer(step.key, answer)
        _redraw_or_finish(form, step)

        return
    assert screen is not None
    if screen.flavour == "modal":
        form.effects.append(OpenModal(screen, "modal", f"section:{index}"))
    else:
        form.session = form.session.with_status(
            Status.ACTIVE, awaiting=f"section:{index}"
        )
        form.effects.append(Render(screen))


def _on_section_changed(form: Form) -> None:
    """A picker or modal of a card section reported a value."""
    event = form.event
    assert isinstance(event, ev.Answered)
    step = form.step(form.session.cursor)
    if not isinstance(step, CardStep):
        form.effects.append(Notice("stale"))
        return
    index = int(str(event.step_key).split(":", 1)[1])
    changes = configuration_card.apply_change(
        step, index, event.payload, form.session, form.step_context()
    )

    if isinstance(changes, Refusal):
        form.refuse(changes, step)
        return
    answer = configuration_card.draft(step, changes, form.session, form.step_context())
    form.session = form.session.with_answer(step.key, answer).with_status(Status.ACTIVE)
    _redraw_or_finish(form, step)


def _on_section_reset(form: Form) -> None:
    event = form.event
    assert isinstance(event, ev.SectionReset)
    step = form.step(form.session.cursor)
    if not isinstance(step, CardStep):
        form.effects.append(Notice("stale"))
        return
    changes = configuration_card.reset_section(step, event.index)
    answer = configuration_card.draft(step, changes, form.session, form.step_context())
    form.session = form.session.with_answer(step.key, answer)
    form.rerender()


def _on_picker_closed(form: Form) -> None:
    parent_id = form.session.parent_id
    if _is_part_edit(form) and parent_id is not None:
        form.session = form.session.with_status(Status.CANCELLED)
        form.effects.append(ResumeParent(parent_id, "edit", {}, cancelled=True))
        return
    form.session = form.session.with_status(Status.ACTIVE)
    form.rerender()


def _on_back(form: Form) -> None:
    current = form.index(form.session.cursor)
    previous = form.previous_key(current)

    if previous is None:
        form.effects.append(Ack())
        return
    target = form.index(previous)
    forgotten: list[str] = []

    for step in form.steps[target + 1 : current + 1]:
        forgotten.extend(produced_keys(step))
    form.session = form.session.without(tuple(forgotten))
    form.emit("setup.step_back", step_key=form.session.cursor)
    form.show(previous)


def _on_cancel(form: Form) -> None:
    form.session = form.session.with_status(Status.AWAITING, awaiting="discard")
    form.effects.append(Confirm(discard_screen(form.locale)))


def _on_discard_confirmed(form: Form) -> None:
    form.emit("setup.discarded", step_key=form.session.cursor)
    form.session = form.session.with_status(Status.CANCELLED)
    form.effects.append(Finalize("discarded"))


def _on_review_confirmed(form: Form) -> None:
    if form.session.mode.kind == "dry_run":
        form.session = form.session.with_status(Status.COMPLETED)
        form.effects.append(Finalize("preview"))
        return
    form.commit("setup", {"answers": form.session.answers})


def _on_commit_succeeded(form: Form) -> None:
    event = form.event
    assert isinstance(event, ev.CommitSucceeded)
    on_saved(form, event.kind)
    if event.kind == "setup":
        form.emit(
            "setup.completed",
            configured_steps=configured_steps(form.steps, form.session.answers),
            **_item_count(form),
        )


def _item_count(form: Form) -> dict[str, int]:
    composition = form.definition.composition
    answer = form.session.answers.get(composition.key) if composition else None
    if answer is None or not isinstance(answer.raw, (list, tuple)):
        return {}
    return {"item_count": len(answer.raw)}


HANDLERS: dict[type[ev.Event], Callable[[Form], None]] = {
    ev.Started: _on_started,
    ev.Answered: _on_answered,
    ev.Drafted: _on_drafted,
    ev.SectionOpened: _on_section_opened,
    ev.SectionReset: _on_section_reset,
    ev.PickerClosed: _on_picker_closed,
    ev.Back: _on_back,
    ev.Cancel: _on_cancel,
    ev.KeepEditing: on_keep_editing,
    ev.DiscardConfirmed: _on_discard_confirmed,
    ev.ReviewConfirmed: _on_review_confirmed,
    ev.EditRequested: on_edit_requested,
    ev.AddRequested: on_add_requested,
    ev.RemoveRequested: on_remove_requested,
    ev.TargetChosen: on_target_chosen,
    ev.MemberChosen: on_member_chosen,
    ev.ItemRemoved: on_item_removed,
    ev.ChildFinished: on_child_finished,
    ev.ScreenRequested: on_screen_requested,
    ev.Aside: on_aside,
    ev.CommitSucceeded: _on_commit_succeeded,
    ev.CommitFailed: on_commit_failed,
    ev.Expired: on_expired,
}

INTERNAL: tuple[type[ev.Event], ...] = (
    ev.ChildFinished,
    ev.CommitSucceeded,
    ev.CommitFailed,
    ev.Expired,
)
ALWAYS_ALLOWED: tuple[type[ev.Event], ...] = (ev.Aside, ev.Expired)

EngineType = TypeVar("EngineType", bound=Engine)


def settle(
    engine: EngineType, handlers: Mapping[type[ev.Event], Callable[[EngineType], None]]
) -> Decision:
    """Guard, handle and close one decision: what every session shares."""
    session, event, context = engine.session, engine.event, engine.context
    rejected = _rejection(session, event)

    if rejected is not None:
        return _rejected(engine, rejected)
    if session.awaiting == "child" and isinstance(event, (ev.Answered, ev.Drafted)):
        resumed = session.remember(event.event_id).touched(
            context.now, context.ttl_seconds
        )

        return Decision(resumed, (ResumeChild(),))
    handler = handlers.get(type(event))

    if handler is None:
        return Decision(session, (Notice("stale"),), rejected="unknown")
    handler(engine)
    return engine.decided()


def _rejected(engine: Engine, reason: str) -> Decision:
    """A duplicate does nothing; a stale click gets a notice and the screen again."""
    session = engine.session
    if reason == "duplicate":
        return Decision(session, (), rejected=reason)
    if reason == "closed" and session.status is Status.EXPIRED:
        return Decision(session, (Finalize("expired"),), rejected=reason)
    if reason != "stale" or session.awaiting == "child":
        return Decision(session, (Notice(reason),), rejected=reason)

    engine.rerender()
    effects = (Notice(reason), *engine.effects)
    after = session.rendered() if engine.effects else session
    return Decision(after, effects, rejected=reason)


def _rejection(session: FormSession, event: ev.Event) -> str | None:
    if session.has_seen(event.event_id):
        return "duplicate"
    if session.is_closed and not isinstance(event, ALWAYS_ALLOWED):
        return "closed"
    if isinstance(event, INTERNAL):
        return None
    stale = (
        event.expected_revision is not None
        and event.expected_revision < session.screen_revision
    )
    if stale and not isinstance(event, ALWAYS_ALLOWED):
        return "stale"
    if session.status is Status.COMMITTING and not isinstance(event, ALWAYS_ALLOWED):
        return "busy"
    return None


def shown_answers(
    definition: FormDefinition,
    session: FormSession,
    context: Context | None = None,
) -> Mapping[str, Answer]:
    """The answers as the screen shows them right now.

    While a card is open its answers live as one draft under the card's key,
    and until the first change there is no draft at all: what the admin reads
    on the screen is the card's initial state, defaults included.
    """
    shown = ev.ScreenRequested("shown")
    form = Form(definition, session, shown, context or Context())
    answers = dict(session.answers)
    steps = form.steps or definition.steps
    for step in steps:
        if isinstance(step, CardStep):
            state = configuration_card.state_of(step, session, form.step_context())
            answers[step.key] = Answer(None, state)
    return answers


def decide(
    definition: FormDefinition,
    session: FormSession,
    event: ev.Event,
    context: Context | None = None,
) -> Decision:
    """The form session after `event`, the effects to run, the rules evaluated."""
    return settle(Form(definition, session, event, context or Context()), HANDLERS)

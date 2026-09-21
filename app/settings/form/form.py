"""`decide(definition, session, event, context) -> Decision`: the whole engine.

Pure: no I/O, no Discord, no clock of its own. Every guard the hostile
environment needs lives here first: a seen event is a no-op, a click from an
older screen is stale, a closed session answers nothing but a notice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

from app.constants import ViewConstants
from app.settings.form import events as ev
from app.settings.form import manager
from app.settings.form.actions import Refusal, configuration_card, registry
from app.settings.form.actions.action import PanelRow, RenderContext
from app.settings.form.components import Button, Screen
from app.settings.form.conditions import Scope, evaluate, explain
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
    Manage,
    Mode,
    Status,
    new_session,
    raw_value,
)
from app.settings.form.form_yaml import (
    CardStep,
    CompositionStep,
    FormDefinition,
    Step,
    produced_keys,
)
from app.settings.form.responses.responses import (
    document_values,
)
from app.settings.form.responses.summary import configured_steps

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
class Context:
    """What the adapter prefetched before calling `decide`."""

    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: int = ViewConstants.LONG_TIMEOUT_SECONDS
    document: Mapping[str, Any] = field(default_factory=dict)
    parent_values: Mapping[str, Any] = field(default_factory=dict)
    items: Sequence[Mapping[str, Any]] = ()
    external: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    server_name: str = ""
    previews: Mapping[str, str] = field(default_factory=dict)
    panel_rows: Sequence[PanelRow] | None = None
    panel_info: str = ""
    panel_info_title: str = ""
    extra_buttons: Sequence[Button] = ()
    enabled: bool = True


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
        chosen = [
            step
            for step in definition.steps
            if step.key in wanted or _depends_on(step, wanted)
        ]
        return tuple(chosen)
    if isinstance(mode, Manage):
        return ()
    return definition.steps


def _depends_on(step: Step, keys: set[str]) -> bool:
    from app.settings.form.form_yaml import _leaves

    return any(leaf.key in keys for leaf in _leaves(step.when))


class Engine:
    """One decision, built piece by piece so every handler stays small."""

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
        self.context = context
        self.steps = steps_for(definition, session.mode)
        self.effects: list[Effect] = []
        self.rules: list[RuleTrace] = []
        self.analytics: list[tuple[str, Mapping[str, Any]]] = []

    @property
    def locale(self) -> str:
        """The locale of the session."""
        return self.session.origin.locale

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

    def render_context(self, can_go_back: bool = False) -> RenderContext:
        """What a kind may read while rendering the current step."""
        index = (
            self.session.mode.index if isinstance(self.session.mode, EditItem) else None
        )
        return RenderContext(
            definition=self.definition,
            steps=self.steps,
            locale=self.locale,
            scope=self.scope(),
            parent_values=self.context.parent_values,
            document=document_values(self.context.document),
            items=self.context.items,
            external=self.context.external,
            server_name=self.context.server_name,
            previews=self.context.previews,
            panel_rows=self.context.panel_rows,
            panel_info=self.context.panel_info,
            panel_info_title=self.context.panel_info_title,
            extra_buttons=self.context.extra_buttons,
            enabled=self.context.enabled,
            can_go_back=can_go_back,
            item_index=index,
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

    def emit(self, name: str, **props: Any) -> None:
        """Record a product event for the adapter to emit."""
        self.analytics.append((name, props))

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
            step, self.session, self.render_context(self.can_go_back(key))
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

    def commit(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Ask the feature to write; the session waits for the outcome."""
        self.session = self.session.with_status(Status.COMMITTING)
        self.effects.append(Commit(kind, payload))

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
        """Draw the current cursor again, without counting a step view."""
        key = self.session.cursor
        if isinstance(self.session.mode, Manage):
            self.effects.append(Render(self.panel()))
            return
        step = self.step(key)
        if step is None or isinstance(step, CompositionStep):
            return
        screen = registry()[step.kind].render(
            step, self.session, self.render_context(self.can_go_back(key))
        )
        if screen.flavour == "modal":
            self.effects.append(OpenModal(screen, "modal", step.key))
        else:
            self.effects.append(Render(screen))

    def panel(self) -> Screen:
        """The manager panel over the saved document."""
        return manager.panel_screen(
            self.definition, self.context.document, self.render_context()
        )

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
                    index
                    for index, existing in enumerate(items)
                    if wanted is not None
                    and raw_value(existing.get(unique)) == wanted
                    and index != index
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


Handler = Callable[[Engine], None]


def _on_started(engine: Engine) -> None:
    session = engine.session
    if isinstance(session.mode, Manage):
        engine.session = session.at(None)
        engine.effects.append(Render(engine.panel()))
        engine.emit("feature.manager_opened", enabled=engine.context.enabled)
        return
    if session.parent_id is None and session.mode.kind == "setup":
        engine.emit("feature.setup_opened")
    engine.advance(-1)


def _on_screen_requested(engine: Engine) -> None:
    engine.rerender()


def _on_answered(engine: Engine) -> None:
    event = engine.event
    assert isinstance(event, ev.Answered)
    if isinstance(engine.session.mode, Manage):
        manager.on_manager_confirmed(engine)
        return
    step = engine.step(engine.session.cursor)
    if step is not None and step.kind in NOT_ON_BACK and step.key != event.step_key:
        engine.rerender()
        return
    if step is None or step.key != event.step_key:
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return
    if step.kind == "review":
        _on_review_confirmed(engine)
        return
    context = engine.render_context()
    parsed = registry()[step.kind].parse(step, event.payload, engine.session, context)

    if isinstance(parsed, Refusal):
        engine.refuse(parsed, step)
        return
    engine.session = engine.session.with_answers(parsed)
    engine.emit("setup.step_completed", step_key=step.key, step_action=step.kind)
    engine.advance(engine.index(step.key))


def _on_drafted(engine: Engine) -> None:
    event = engine.event
    assert isinstance(event, ev.Drafted)
    if isinstance(engine.session.mode, Manage):
        answers = {key: Answer(value) for key, value in event.changes.items()}
        engine.session = engine.session.with_answers(answers)
        engine.effects.append(Ack())

        return
    step = engine.step(engine.session.cursor)
    if step is None or step.key != event.step_key:
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return
    if isinstance(step, CardStep):
        context = engine.render_context()
        changes = configuration_card.apply_drafts(
            step, event.changes, engine.session, context
        )

        if isinstance(changes, Refusal):
            engine.refuse(changes, step)
            return
        answer = configuration_card.draft(step, changes, engine.session, context)
        engine.session = engine.session.with_answer(step.key, answer)
        engine.rerender()

        return
    answers = {key: Answer(value) for key, value in event.changes.items()}
    engine.session = engine.session.with_answers(answers)

    if step.kind == "single_choice":
        engine.rerender()
        return
    engine.effects.append(Ack())


def _on_section_opened(engine: Engine) -> None:
    event = engine.event
    assert isinstance(event, ev.SectionOpened)
    step = engine.step(engine.session.cursor)
    if not isinstance(step, CardStep) or step.key != event.step_key:
        engine.effects.append(Notice("stale"))
        engine.rerender()
        return
    screen, changes = configuration_card.open_section(
        step, event.index, engine.session, engine.render_context()
    )
    if changes is not None:
        answer = configuration_card.draft(
            step, changes, engine.session, engine.render_context()
        )
        engine.session = engine.session.with_answer(step.key, answer)
        engine.rerender()

        return
    assert screen is not None
    if screen.flavour == "modal":
        engine.effects.append(OpenModal(screen, "modal", f"section:{event.index}"))
    else:
        engine.session = engine.session.with_status(
            Status.ACTIVE, awaiting=f"section:{event.index}"
        )
        engine.effects.append(Render(screen))


def _on_section_changed(engine: Engine) -> None:
    """A picker or modal of a card section reported a value."""
    event = engine.event
    assert isinstance(event, ev.Answered)
    step = engine.step(engine.session.cursor)
    if not isinstance(step, CardStep):
        engine.effects.append(Notice("stale"))
        return
    index = int(str(event.step_key).split(":", 1)[1])
    changes = configuration_card.apply_change(
        step, index, event.payload, engine.session, engine.render_context()
    )

    if isinstance(changes, Refusal):
        engine.refuse(changes, step)
        return
    answer = configuration_card.draft(
        step, changes, engine.session, engine.render_context()
    )
    engine.session = engine.session.with_answer(step.key, answer).with_status(
        Status.ACTIVE
    )
    engine.rerender()


def _on_section_reset(engine: Engine) -> None:
    event = engine.event
    assert isinstance(event, ev.SectionReset)
    step = engine.step(engine.session.cursor)
    if not isinstance(step, CardStep):
        engine.effects.append(Notice("stale"))
        return
    changes = configuration_card.reset_section(step, event.index)
    answer = configuration_card.draft(
        step, changes, engine.session, engine.render_context()
    )
    engine.session = engine.session.with_answer(step.key, answer)
    engine.rerender()


def _on_picker_closed(engine: Engine) -> None:
    engine.session = engine.session.with_status(Status.ACTIVE)
    engine.rerender()


def _on_back(engine: Engine) -> None:
    current = engine.index(engine.session.cursor)
    previous = engine.previous_key(current)

    if previous is None:
        engine.effects.append(Ack())
        return
    target = engine.index(previous)
    forgotten: list[str] = []

    for step in engine.steps[target + 1 : current + 1]:
        forgotten.extend(produced_keys(step))
    engine.session = engine.session.without(tuple(forgotten))
    engine.emit("setup.step_back", step_key=engine.session.cursor)
    engine.show(previous)


def _on_cancel(engine: Engine) -> None:
    engine.session = engine.session.with_status(Status.AWAITING, awaiting="discard")
    engine.effects.append(Confirm(manager.discard_screen(engine.locale)))


def _on_keep_editing(engine: Engine) -> None:
    engine.session = engine.session.with_status(Status.ACTIVE)
    engine.effects.append(Dismiss())
    engine.emit("setup.discard_recovered", step_key=engine.session.cursor)


def _on_discard_confirmed(engine: Engine) -> None:
    engine.emit("setup.discarded", step_key=engine.session.cursor)
    engine.session = engine.session.with_status(Status.CANCELLED)
    engine.effects.append(Finalize("discarded"))


def _on_review_confirmed(engine: Engine) -> None:
    if engine.session.mode.kind == "dry_run":
        engine.session = engine.session.with_status(Status.COMPLETED)
        engine.effects.append(Finalize("preview"))
        return
    engine.commit("setup", {"answers": engine.session.answers})


def _on_commit_succeeded(engine: Engine) -> None:
    event = engine.event
    assert isinstance(event, ev.CommitSucceeded)
    engine.session = engine.session.with_status(Status.COMPLETED)
    engine.effects.append(Finalize(FINAL_KIND.get(event.kind, event.kind)))
    if event.kind == "setup":
        engine.emit(
            "setup.completed",
            configured_steps=configured_steps(engine.steps, engine.session.answers),
            **_item_count(engine),
        )


def _item_count(engine: Engine) -> dict[str, int]:
    composition = engine.definition.composition
    answer = engine.session.answers.get(composition.key) if composition else None
    if answer is None or not isinstance(answer.raw, (list, tuple)):
        return {}
    return {"item_count": len(answer.raw)}


def _on_commit_failed(engine: Engine) -> None:
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


def _on_aside(engine: Engine) -> None:
    event = engine.event
    assert isinstance(event, ev.Aside)
    engine.effects.append(RunAside(event.name))


def _on_expired(engine: Engine) -> None:
    engine.emit("setup.abandoned", step_key=engine.session.cursor)
    engine.session = engine.session.with_status(Status.EXPIRED)
    engine.effects.append(Finalize("expired"))


HANDLERS: dict[type[ev.Event], Handler] = {
    ev.Started: _on_started,
    ev.Answered: _on_answered,
    ev.Drafted: _on_drafted,
    ev.SectionOpened: _on_section_opened,
    ev.SectionReset: _on_section_reset,
    ev.PickerClosed: _on_picker_closed,
    ev.Back: _on_back,
    ev.Cancel: _on_cancel,
    ev.KeepEditing: _on_keep_editing,
    ev.DiscardConfirmed: _on_discard_confirmed,
    ev.ReviewConfirmed: _on_review_confirmed,
    ev.EditRequested: manager.on_edit_requested,
    ev.AddRequested: manager.on_add_requested,
    ev.RemoveRequested: manager.on_remove_requested,
    ev.TargetChosen: manager.on_target_chosen,
    ev.MemberChosen: manager.on_member_chosen,
    ev.ItemRemoved: manager.on_item_removed,
    ev.ChildFinished: manager.on_child_finished,
    ev.ScreenRequested: _on_screen_requested,
    ev.Lifecycle: manager.on_lifecycle,
    ev.LifecycleConfirmed: manager.on_lifecycle_confirmed,
    ev.Aside: _on_aside,
    ev.CommitSucceeded: _on_commit_succeeded,
    ev.CommitFailed: _on_commit_failed,
    ev.Expired: _on_expired,
}

INTERNAL: tuple[type[ev.Event], ...] = (
    ev.ChildFinished,
    ev.CommitSucceeded,
    ev.CommitFailed,
    ev.Expired,
)
ALWAYS_ALLOWED: tuple[type[ev.Event], ...] = (ev.Aside, ev.Expired)


def _rejected(
    definition: FormDefinition,
    session: FormSession,
    event: ev.Event,
    context: Context,
    reason: str,
) -> Decision:
    """A duplicate does nothing; a stale click gets a notice and the screen again."""
    if reason == "duplicate":
        return Decision(session, (), rejected=reason)
    if reason == "closed" and session.status is Status.EXPIRED:
        return Decision(session, (Finalize("expired"),), rejected=reason)
    if reason != "stale" or session.awaiting == "child":
        return Decision(session, (Notice(reason),), rejected=reason)
    engine = Engine(definition, session, event, context)
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


def decide(
    definition: FormDefinition,
    session: FormSession,
    event: ev.Event,
    context: Context | None = None,
) -> Decision:
    """The session after `event`, the effects to run, the rules it evaluated."""
    context = context or Context()
    rejected = _rejection(session, event)

    if rejected is not None:
        return _rejected(definition, session, event, context, rejected)
    if session.awaiting == "child" and isinstance(event, (ev.Answered, ev.Drafted)):
        resumed = session.remember(event.event_id).touched(
            context.now, context.ttl_seconds
        )

        return Decision(resumed, (ResumeChild(),))
    engine = Engine(definition, session, event, context)
    handler = HANDLERS.get(type(event))

    if handler is None:
        return Decision(session, (Notice("stale"),), rejected="unknown")
    if isinstance(event, ev.Answered) and event.step_key.startswith("section:"):
        _on_section_changed(engine)
    else:
        handler(engine)
    session_after = engine.session.remember(event.event_id)
    if not session_after.is_closed:
        session_after = session_after.touched(context.now, context.ttl_seconds)
    if any(isinstance(effect, Render) for effect in engine.effects):
        session_after = session_after.rendered()
    return Decision(
        session_after,
        tuple(engine.effects),
        tuple(engine.rules),
        tuple(engine.analytics),
    )

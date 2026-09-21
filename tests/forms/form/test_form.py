"""The engine, event by event, on the real definitions."""

from datetime import datetime, timedelta, timezone

import pytest

from app.settings.form import events as ev
from app.settings.form.components import Card, Choice, Gallery, Panel, Picker
from app.settings.form.effects import (
    Commit,
    OpenModal,
    Render,
    ResumeParent,
    ShowError,
)
from app.settings.form.form import Context, decide
from app.settings.form.form_state import (
    AddItem,
    Answer,
    Edit,
    Manage,
    Origin,
    Setup,
    Status,
    new_session,
)
from app.settings.form.form_yaml import DefinitionRegistry
from app.settings.form.responses.responses import to_document
from tests.forms.replay import replay

pytestmark = pytest.mark.unit

REGISTRY = DefinitionRegistry()
ORIGIN = Origin(guild_id="123456789", user_id="555", locale="pt-br")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
COUNTER = {"n": 0}


def evt(event_type, **fields):
    COUNTER["n"] += 1
    return event_type(event_id=f"e{COUNTER['n']}", **fields)


def start(form, mode=Setup(), answers=None, context=None, parent_id=None):
    definition = REGISTRY.get(form)
    session = new_session(
        (definition.key, definition.version),
        mode,
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers=answers,
        parent_id=parent_id,
    )
    return definition, decide(definition, session, evt(ev.Started), context)


def kinds(decision):
    return [type(effect).__name__ for effect in decision.effects]


def screen(decision):
    for effect in decision.effects:
        if isinstance(effect, (Render, OpenModal)):
            return effect.screen
    raise AssertionError(f"no screen in {kinds(decision)}")


# ---------------------------------------------------------------- setup flow


def test_started_renders_the_intro_and_reports_the_opening():
    definition, decision = start("block_links")
    assert decision.session.cursor == "form"
    assert kinds(decision) == ["Render"]
    assert [name for name, _ in decision.analytics] == [
        "feature.setup_opened",
        "setup.step_viewed",
    ]
    assert "Bloquear Links" in screen(decision).title
    assert [b.action for b in screen(decision).buttons] == ["confirm", "cancel"]


def test_answering_moves_to_the_next_allowed_step_and_skips_the_others():
    definition, decision = start("block_links")
    session = decision.session
    decision = decide(definition, session, evt(ev.Answered, step_key="form"))
    assert decision.session.cursor == "link_settings"
    assert isinstance(screen(decision).components[0], Card)
    decision = decide(
        definition, decision.session, evt(ev.Answered, step_key="link_settings")
    )
    assert decision.session.cursor == "add_custom"
    decision = decide(
        definition,
        decision.session,
        evt(ev.Answered, step_key="add_custom", payload=False),
    )
    assert decision.session.cursor == "permissions", "the composition is skipped"
    assert any(
        rule.step_key == "custom_links" and rule.skipped
        for rule in decision.evaluated_rules
    )


def test_a_draft_stores_the_selection_without_moving():
    definition, decision = start("block_links")
    events = [
        evt(ev.Answered, step_key="form"),
        evt(ev.Answered, step_key="link_settings"),
        evt(ev.Answered, step_key="add_custom", payload=False),
    ]
    last = replay(definition, decision.session, events)[-1]
    drafted = decide(
        definition,
        last.session,
        evt(ev.Drafted, step_key="permissions", changes={"allowed_chats": ["100"]}),
    )
    assert kinds(drafted) == ["Ack"]
    assert drafted.session.cursor == "permissions"
    confirmed = decide(
        definition, drafted.session, evt(ev.Answered, step_key="permissions")
    )
    assert confirmed.session.raw("allowed_chats") == "100"
    assert confirmed.session.cursor == "confirm"


def _to_review(form="block_links"):
    definition, decision = start(form)
    events = [
        evt(ev.Answered, step_key="form"),
        evt(ev.Answered, step_key="link_settings"),
        evt(ev.Answered, step_key="add_custom", payload=False),
        evt(ev.Answered, step_key="permissions"),
    ]
    return definition, replay(definition, decision.session, events)[-1].session


def test_the_review_confirms_into_a_commit_and_completes_on_success():
    definition, session = _to_review()
    committing = decide(definition, session, evt(ev.Answered, step_key="confirm"))
    assert committing.session.status is Status.COMMITTING
    assert kinds(committing) == ["Commit"]
    done = decide(definition, committing.session, evt(ev.CommitSucceeded, kind="setup"))
    assert done.session.status is Status.COMPLETED
    assert kinds(done) == ["Finalize"] and done.effects[0].kind == "enabled"
    assert "setup.completed" in [name for name, _ in done.analytics]


def test_a_failed_commit_fails_the_session_visibly():
    definition, session = _to_review()
    committing = decide(definition, session, evt(ev.Answered, step_key="confirm"))
    failed = decide(
        definition, committing.session, evt(ev.CommitFailed, kind="setup", error="boom")
    )
    assert failed.session.status is Status.FAILED
    assert failed.effects[0].kind == "error"


def test_a_completed_setup_names_the_steps_it_configured_never_their_values():
    definition, session = _to_review()
    committing = decide(definition, session, evt(ev.Answered, step_key="confirm"))
    done = decide(definition, committing.session, evt(ev.CommitSucceeded, kind="setup"))
    props = dict(done.analytics)["setup.completed"]

    configured = props["configured_steps"]
    declared = [step.key for step in definition.steps]
    assert "link_settings" in configured
    assert "form" not in configured and "confirm" not in configured, (
        "an intro or a review configures nothing"
    )
    assert "add_custom" not in configured, "a hidden gate is not a setting"
    assert list(configured) == [key for key in declared if key in configured], (
        "the summary follows the order the form declares"
    )
    assert all(isinstance(value, (int, str, tuple, list)) for value in props.values())
    assert set(configured) <= set(declared), "step keys only, never an answer"


def test_a_failed_commit_reports_the_kind_the_step_and_the_reason():
    definition, session = _to_review()
    committing = decide(definition, session, evt(ev.Answered, step_key="confirm"))
    failed = decide(
        definition,
        committing.session,
        evt(ev.CommitFailed, kind="setup", error="RuntimeError"),
    )

    assert (
        "feature.commit_failed",
        {"commit_kind": "setup", "error_type": "RuntimeError", "step_key": "confirm"},
    ) in failed.analytics


def test_the_document_of_a_default_setup_keeps_the_stored_shape():
    definition, session = _to_review()
    document = to_document(definition.steps, session.answers, "pt-br")
    assert document["mode"] == "block_all"
    assert document["answer"] == "Nada de links por aqui! :p"
    assert document["custom_links"] == {"style": "composition", "values": []}
    assert document["add_custom"] == {"style": "boolean", "values": False}


# ------------------------------------------------------------ hostile events


def test_a_second_confirm_while_committing_is_refused():
    definition, session = _to_review()
    committing = decide(definition, session, evt(ev.Answered, step_key="confirm"))
    again = decide(definition, committing.session, evt(ev.Answered, step_key="confirm"))
    assert again.rejected == "busy"
    assert again.session is committing.session


def test_the_same_event_id_is_applied_once():
    definition, decision = start("block_links")
    event = evt(ev.Answered, step_key="form")
    first = decide(definition, decision.session, event)
    second = decide(definition, first.session, event)
    assert second.rejected == "duplicate"
    assert second.session.cursor == first.session.cursor
    assert second.effects == ()


def test_a_click_from_an_older_screen_is_stale():
    definition, decision = start("block_links")
    old = decision.session.screen_revision
    moved = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    stale = decide(definition, moved.session, ev.Answered("late", old, "form"))
    assert stale.rejected == "stale"
    assert kinds(stale) == ["Notice", "Render"]
    assert stale.session.cursor == moved.session.cursor
    assert stale.session.revision == moved.session.revision


def test_a_closed_session_refuses_everything_but_says_when_it_expired():
    definition, session = _to_review()
    expired = decide(definition, session, evt(ev.Expired))
    assert expired.session.status is Status.EXPIRED
    assert expired.effects[0].kind == "expired"
    late = decide(definition, expired.session, evt(ev.Answered, step_key="confirm"))
    assert late.rejected == "closed"
    assert late.effects[0].kind == "expired"


# --------------------------------------------------------------- navigation


def test_back_keeps_the_target_answer_as_its_draft_and_the_earlier_ones():
    definition, session = _to_review()
    back = decide(definition, session, evt(ev.Back))
    assert back.session.cursor == "permissions"
    assert "allowed_chats" in back.session.answers, "shown again as the draft"
    assert back.session.raw("mode") == "block_all", "earlier answers survive"
    assert ("setup.step_back", {"step_key": "confirm"}) in back.analytics


def test_back_forgets_the_answers_of_the_steps_it_walks_over():
    definition, decision = start("block_links")
    events = [
        evt(ev.Answered, step_key="form"),
        evt(ev.Answered, step_key="link_settings"),
        evt(ev.Answered, step_key="add_custom", payload=False),
    ]
    at_permissions = replay(definition, decision.session, events)[-1]
    assert at_permissions.session.raw("add_custom") is False
    to_gate = decide(definition, at_permissions.session, evt(ev.Back))
    assert to_gate.session.cursor == "add_custom"
    assert to_gate.session.raw("add_custom") is False, "the target keeps its draft"
    to_card = decide(definition, to_gate.session, evt(ev.Back))
    assert to_card.session.cursor == "link_settings"
    assert "add_custom" not in to_card.session.answers, "walked over, forgotten"
    assert to_card.session.raw("mode") == "block_all", "the card keeps every value"


def test_back_never_lands_on_a_modal_step():
    definition = REGISTRY.get("notifications_twitch")
    child = new_session(
        (definition.key, 1),
        AddItem(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        parent_id="parent",
    )
    started = decide(definition, child, evt(ev.Started))
    assert started.session.cursor == "channel"
    drafted = decide(
        definition,
        started.session,
        evt(ev.Drafted, step_key="channel", changes={"channel": ["100"]}),
    )
    at_streamer = decide(
        definition, drafted.session, evt(ev.Answered, step_key="channel")
    )
    assert kinds(at_streamer) == ["OpenModal"]
    context = Context(external={"twitch": {"user_id": "1"}})
    at_info = decide(
        definition,
        at_streamer.session,
        evt(ev.Answered, step_key="streamer", payload={"inputs": ["gaules"]}),
        context,
    )
    assert at_info.session.cursor == "continue_to_messages"
    back = decide(definition, at_info.session, evt(ev.Back))
    assert back.session.cursor == "channel"
    assert "streamer" not in back.session.answers


def test_back_at_the_first_step_only_acknowledges():
    definition, decision = start("block_links")
    at_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    back = decide(definition, at_card.session, evt(ev.Back))
    assert kinds(back) == ["Ack"]
    assert back.session.cursor == "link_settings"


# ------------------------------------------------------------------- cancel


def test_cancel_asks_then_keeps_or_discards():
    definition, session = _to_review()
    asked = decide(definition, session, evt(ev.Cancel))
    assert asked.session.status is Status.AWAITING and kinds(asked) == ["Confirm"]
    kept = decide(definition, asked.session, evt(ev.KeepEditing))
    assert kept.session.status is Status.ACTIVE and kinds(kept) == ["Dismiss"]
    asked_again = decide(definition, kept.session, evt(ev.Cancel))
    discarded = decide(definition, asked_again.session, evt(ev.DiscardConfirmed))
    assert discarded.session.status is Status.CANCELLED
    assert discarded.effects[0].kind == "discarded"


# ---------------------------------------------------------------- validation


def test_a_refused_value_shows_the_error_and_keeps_the_screen():
    definition = REGISTRY.get("stream_elements_commands")
    _, decision = start("stream_elements_commands")
    at_modal = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    assert kinds(at_modal) == ["OpenModal"]
    refused = decide(
        definition,
        at_modal.session,
        evt(ev.Answered, step_key="streamer", payload={"inputs": ["nobody"]}),
        Context(external={"twitch": {"user_id": None}}),
    )
    assert (
        kinds(refused) == ["ShowError"]
        and refused.effects[0].key == "streamer-not-found"
    )
    assert refused.session.cursor == "streamer"
    assert refused.session.screen_revision == at_modal.session.screen_revision
    accepted = decide(
        definition,
        refused.session,
        evt(ev.Answered, step_key="streamer", payload={"inputs": ["Shroud"]}),
        Context(external={"twitch": {"user_id": "1"}}),
    )
    assert accepted.session.raw("streamer") == "shroud"
    assert accepted.session.cursor == "confirm"


# --------------------------------------------------------------------- card


def test_card_done_refuses_missing_required_fields_with_their_labels():
    definition, decision = start("reminders_birthday")
    at_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    refused = decide(
        definition, at_card.session, evt(ev.Answered, step_key="birthday_config")
    )
    error = refused.effects[0]
    assert isinstance(error, ShowError) and error.plain
    assert (
        "**Canal**" in error.args["fields"]
        and "**Horário da notificação**" in error.args["fields"]
    )
    assert (
        "setup.required_missing",
        {"card_key": "birthday_config"},
    ) in refused.analytics


def test_card_sections_open_pickers_and_changes_redraw_the_card():
    definition, decision = start("reminders_birthday")
    at_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    picker = decide(
        definition,
        at_card.session,
        evt(ev.SectionOpened, step_key="birthday_config", index=0),
    )
    assert isinstance(screen(picker).components[0], Picker)
    chosen = decide(
        definition,
        picker.session,
        evt(ev.Answered, step_key="section:0", payload=["100"]),
    )
    assert isinstance(screen(chosen).components[0], Card)
    assert chosen.session.answers["birthday_config"].part("channel") == "100"
    toggled = decide(
        definition,
        chosen.session,
        evt(ev.SectionOpened, step_key="birthday_config", index=3),
    )
    assert toggled.session.answers["birthday_config"].part("mention_everyone") is True


def test_card_done_stores_the_transformed_date_and_the_fields():
    definition = REGISTRY.get("reminders_birthday")
    child = new_session(
        (definition.key, 1),
        AddItem(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        parent_id="p",
        answers={"user": Answer("777")},
    )
    started = decide(definition, child, evt(ev.Started))
    assert started.session.cursor == "user"
    at_card = decide(definition, started.session, evt(ev.Answered, step_key="user"))
    month = decide(
        definition,
        at_card.session,
        evt(ev.Answered, step_key="section:0", payload=["05"]),
    )
    day = decide(
        definition,
        month.session,
        evt(ev.Answered, step_key="section:1", payload={"inputs": ["12"]}),
    )
    done = decide(
        definition, day.session, evt(ev.Answered, step_key="birthday_member_config")
    )
    assert done.session.status is Status.COMPLETED
    handed = done.effects[0]
    assert isinstance(handed, ResumeParent) and handed.answers["date"].raw == "05-12"


def test_an_invalid_day_is_refused_inside_the_card():
    definition = REGISTRY.get("reminders_birthday")
    child = new_session(
        (definition.key, 1),
        AddItem(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        parent_id="p",
        answers={"user": Answer("777")},
    )
    started = decide(definition, child, evt(ev.Started))
    at_card = decide(definition, started.session, evt(ev.Answered, step_key="user"))
    month = decide(
        definition,
        at_card.session,
        evt(ev.Answered, step_key="section:0", payload=["02"]),
    )
    refused = decide(
        definition,
        month.session,
        evt(ev.Answered, step_key="section:1", payload={"inputs": ["31"]}),
    )
    assert kinds(refused) == ["ShowError"] and refused.effects[0].key == "invalid-date"
    assert refused.session.answers["birthday_member_config"].part("day") is None


# ------------------------------------------------------------- compositions


def test_a_composition_opens_a_child_and_its_result_lands_on_the_review():
    definition, decision = start("notifications_twitch")
    opened = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    assert opened.session.status is Status.AWAITING and kinds(opened) == ["OpenChild"]
    child = opened.effects[0].session
    assert child.parent_id == opened.session.id and child.mode == AddItem()
    item = {
        "channel": Answer("100"),
        "streamer": Answer("gaules"),
        "notification_messages": Answer("hi"),
    }
    back = decide(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="add_item", answers=item),
    )
    assert back.session.cursor == "confirm"
    assert len(back.session.answers["notifications"].raw) == 1
    review = screen(back)
    assert [b.action for b in review.buttons] == ["edit", "add", "confirm", "cancel"]
    assert "gaules" in review.description


def test_the_review_removes_items_by_index_and_offers_remove_only_with_two():
    definition, decision = start("notifications_twitch")
    opened = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    items = [
        {"channel": Answer("100"), "streamer": Answer(name)}
        for name in ("gaules", "cellbit")
    ]
    one = decide(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="add_item", answers=items[0]),
    )
    added = decide(definition, one.session, evt(ev.AddRequested))
    two = decide(
        definition,
        added.session,
        evt(ev.ChildFinished, child_mode="add_item", answers=items[1]),
    )
    assert [b.action for b in screen(two).buttons] == [
        "edit",
        "add",
        "remove",
        "confirm",
        "cancel",
    ]
    picker = decide(definition, two.session, evt(ev.RemoveRequested))
    removed = decide(
        definition, picker.session, evt(ev.TargetChosen, value="notifications$0")
    )
    assert [
        i["streamer"].raw for i in removed.session.answers["notifications"].raw
    ] == ["cellbit"]


# ------------------------------------------------------------------- manager

BLOCK_LINKS_DOC = {
    "guild_id": "123456789",
    "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_roles": {"style": "role", "values": "201"},
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "custom_links": {
        "style": "composition",
        "values": [
            {
                "link": {
                    "value": "meusite.com.br",
                    "title": "Link ou Site",
                    "style": "code",
                }
            },
            {
                "link": {
                    "value": "docs.example.org",
                    "title": "Link ou Site",
                    "style": "code",
                }
            },
        ],
    },
    "answer": "Nada de links aqui! :p",
}


def test_the_manager_renders_the_panel_with_grouped_settings():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    panel = screen(decision).components[0]
    assert isinstance(panel, Panel)
    assert [g.key for g in panel.groups] == [
        "link_settings",
        "permissions",
        "custom_links",
    ]
    assert (
        "**Modo de bloqueio:** Bloquear todos, com exceções" in panel.groups[0].lines[1]
    )
    assert [b.action for b in screen(decision).buttons] == [
        "lifecycle:pause",
        "lifecycle:disable",
        "add",
        "remove",
        "aside:history",
        "aside:help",
    ]


def test_a_section_edit_opens_a_child_over_that_step_seeded_from_the_document():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    opened = decide(
        definition,
        decision.session,
        evt(ev.EditRequested, target="link_settings"),
        Context(document=BLOCK_LINKS_DOC),
    )
    child = opened.effects[0].session
    assert child.mode == Edit(("link_settings",))
    assert child.raw("answer") == "Nada de links aqui! :p"
    started = decide(
        definition, child, evt(ev.Started), Context(document=BLOCK_LINKS_DOC)
    )
    assert started.session.cursor == "link_settings"
    done = decide(
        definition,
        started.session,
        evt(ev.Answered, step_key="link_settings"),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert (
        isinstance(done.effects[0], ResumeParent)
        and done.effects[0].child_mode == "edit"
    )
    committed = decide(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="edit", answers=done.effects[0].answers),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert (
        isinstance(committed.effects[0], Commit) and committed.effects[0].kind == "edit"
    )


def test_lifecycle_actions_need_the_typed_word():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    asked = decide(definition, decision.session, evt(ev.Lifecycle, action="pause"))
    assert kinds(asked) == ["OpenModal"] and asked.session.status is Status.AWAITING
    wrong = decide(
        definition,
        asked.session,
        evt(ev.LifecycleConfirmed, action="pause", word="nope"),
    )
    assert kinds(wrong) == ["Ack"] and wrong.session.status is Status.ACTIVE
    asked = decide(definition, wrong.session, evt(ev.Lifecycle, action="pause"))
    right = decide(
        definition,
        asked.session,
        evt(ev.LifecycleConfirmed, action="pause", word="pausar"),
    )
    assert isinstance(right.effects[0], Commit) and right.effects[0].kind == "pause"
    done = decide(definition, right.session, evt(ev.CommitSucceeded, kind="pause"))
    assert done.effects[0].kind == "paused"


def test_removing_from_the_manager_commits_the_item():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    picker = decide(
        definition,
        decision.session,
        evt(ev.RemoveRequested),
        Context(document=BLOCK_LINKS_DOC),
    )
    assert screen(picker).components[0].options[0].value == "custom_links$0"
    removed = decide(
        definition,
        picker.session,
        evt(ev.TargetChosen, value="custom_links$0"),
        Context(document=BLOCK_LINKS_DOC),
    )
    commit = removed.effects[0]
    assert isinstance(commit, Commit) and commit.kind == "remove_item"
    assert commit.payload["item"]["link"]["value"] == "meusite.com.br"


def test_asides_never_touch_the_session():
    definition, decision = start(
        "block_links", Manage(), context=Context(document=BLOCK_LINKS_DOC)
    )
    aside = decide(definition, decision.session, evt(ev.Aside, name="history"))
    assert kinds(aside) == ["RunAside"]
    assert aside.session.revision == decision.session.revision


def test_the_gallery_and_the_file_upload_follow_the_design():
    definition, decision = start("welcome_messages")
    at_channel = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    drafted = decide(
        definition,
        at_channel.session,
        evt(
            ev.Drafted,
            step_key="welcome_messages_channel",
            changes={"welcome_messages_channel": ["101"]},
        ),
    )
    gallery = decide(
        definition,
        drafted.session,
        evt(ev.Answered, step_key="welcome_messages_channel"),
    )
    assert isinstance(screen(gallery).components[0], Gallery)
    custom = decide(
        definition,
        gallery.session,
        evt(ev.Answered, step_key="welcome_design", payload="custom_only"),
    )
    assert custom.session.cursor == "welcome_custom_image" and kinds(custom) == [
        "OpenModal"
    ]
    server = decide(
        definition,
        gallery.session,
        evt(ev.Answered, step_key="welcome_design", payload="server_blur"),
    )
    assert server.session.cursor == "question_welcome_messages"


def test_choice_steps_type_their_values():
    definition, decision = start("block_links")
    at_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    gate = decide(
        definition, at_card.session, evt(ev.Answered, step_key="link_settings")
    )
    assert isinstance(screen(gate).components[0], Choice)
    yes = decide(
        definition,
        gate.session,
        evt(ev.Answered, step_key="add_custom", payload="True"),
    )
    assert yes.session.raw("add_custom") is True
    assert kinds(yes) == ["OpenChild"], "the composition opens right away"


def test_an_accepted_event_moves_the_deadline_forward():
    """Found running the local bot: a session died thirty minutes after it
    opened, however recently the admin had clicked, because `expires_at` was
    fixed at creation. A discord.py view, which the old engine used, counted its
    timeout from the last interaction."""
    definition, decision = start("block_links")
    later = NOW + timedelta(seconds=45)

    moved = decide(
        definition,
        decision.session,
        evt(ev.Answered, step_key="form"),
        Context(now=later, ttl_seconds=60),
    )

    assert moved.session.expires_at == later + timedelta(seconds=60)


def test_a_rejected_event_leaves_the_deadline_where_it_was():
    definition, decision = start("block_links")
    answered = evt(ev.Answered, step_key="form")
    first = decide(
        definition,
        decision.session,
        answered,
        Context(now=NOW + timedelta(seconds=10), ttl_seconds=60),
    )

    again = decide(
        definition,
        first.session,
        answered,
        Context(now=NOW + timedelta(seconds=50), ttl_seconds=60),
    )

    assert again.rejected is not None
    assert again.session.expires_at == first.session.expires_at

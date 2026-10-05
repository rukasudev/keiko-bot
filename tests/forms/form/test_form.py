"""The form engine, event by event, on the real definitions."""

from datetime import timedelta

import pytest

from app.settings.form import events as ev
from app.settings.form.actions.action import Context
from app.settings.form.components import (
    Card,
    Choice,
    Gallery,
    Panel,
    Picker,
)
from app.settings.form.effects import (
    Commit,
    ResumeParent,
    ShowError,
)
from app.settings.form.form import decide
from app.settings.form.form_state import (
    AddItem,
    Answer,
    Edit,
    Setup,
    Status,
    new_session,
)
from app.settings.form.responses.responses import to_document
from tests.forms.replay import (
    NOW,
    ORIGIN,
    REGISTRY,
    evt,
    kinds,
    open_part,
    replay,
    screen,
    start,
)

pytestmark = pytest.mark.unit


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
    from app.settings.form.form_yaml import compile_form
    from tests.forms.form.card_fixtures import text

    options = [
        {"label": text("A"), "value": "a"},
        {"label": text("B"), "value": "b"},
    ]
    definition = compile_form(
        "back_over_modal",
        {
            "steps": [
                {
                    "action": "form",
                    "key": "form",
                    "title": text("🧪"),
                    "description": text("i"),
                },
                {
                    "action": "options",
                    "key": "first",
                    "title": text("First"),
                    "description": text("f"),
                    "options": options,
                },
                {
                    "action": "modal",
                    "key": "name",
                    "title": text("Name"),
                    "description": text("n"),
                    "label": text("Name"),
                },
                {
                    "action": "options",
                    "key": "second",
                    "title": text("Second"),
                    "description": text("s"),
                    "options": options,
                },
                {
                    "action": "resume",
                    "key": "confirm",
                    "title": text("ok?"),
                    "description": text("r"),
                },
            ]
        },
    )
    session = new_session(
        (definition.key, definition.version), Setup(), ORIGIN, ttl_seconds=60, now=NOW
    )
    at_first = decide(definition, session, evt(ev.Started))
    at_first = decide(definition, at_first.session, evt(ev.Answered, step_key="form"))
    at_name = decide(
        definition, at_first.session, evt(ev.Answered, step_key="first", payload="a")
    )
    assert kinds(at_name) == ["OpenModal"]
    at_second = decide(
        definition,
        at_name.session,
        evt(ev.Answered, step_key="name", payload={"inputs": ["x"]}),
    )
    assert at_second.session.cursor == "second"

    back = decide(definition, at_second.session, evt(ev.Back))

    assert back.session.cursor == "first"
    assert "name" not in back.session.answers


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
    assert [b.action for b in review.buttons] == [
        "add",
        "aside:preview",
        "confirm",
        "cancel",
    ]
    lines = [line for group in review.components[0].groups for line in group.lines]
    assert any("gaules" in line for line in lines)


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
        "add",
        "remove",
        "aside:preview",
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


def test_an_item_edited_onto_another_items_key_leaves_one_item():
    """The merge looked for another item with the same `unique_by` value but
    compared the loop index with itself, so it never found one."""
    links = tuple(
        {"link": Answer(link)} for link in ("meusite.com.br", "docs.example.org")
    )
    definition, decision = start("block_links", answers={"custom_links": Answer(links)})
    edited = decide(
        definition,
        decision.session,
        evt(
            ev.ChildFinished,
            child_mode="edit_item",
            answers={"link": Answer("meusite.com.br")},
            index=1,
        ),
    )
    listed = edited.session.answers["custom_links"].raw
    assert [item["link"].raw for item in listed] == ["meusite.com.br"]


def test_back_on_the_remove_dropdown_redraws_the_review():
    definition, decision = start("notifications_twitch")
    opened = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    session = opened.session
    for name in ("gaules", "cellbit"):
        added = decide(
            definition,
            session,
            evt(
                ev.ChildFinished,
                child_mode="add_item",
                answers={"channel": Answer("100"), "streamer": Answer(name)},
            ),
        )
        session = decide(definition, added.session, evt(ev.AddRequested)).session
    session = session.with_status(Status.ACTIVE)
    picker = decide(definition, session, evt(ev.RemoveRequested))
    assert [b.action for b in screen(picker).buttons] == ["picker_back"]
    back = decide(definition, picker.session, evt(ev.PickerClosed))
    assert back.session.status is Status.ACTIVE
    assert back.session.cursor == "confirm"
    groups = screen(back).components[0].groups
    assert any("cellbit" in line for group in groups for line in group.lines)


def test_the_gallery_and_the_file_upload_follow_the_design():
    definition, decision = start("welcome_messages")
    at_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    assert isinstance(screen(at_card).components[0], Card)
    gallery = decide(
        definition,
        at_card.session,
        evt(ev.SectionOpened, step_key="welcome_config", index=2),
    )
    assert isinstance(screen(gallery).components[0], Gallery)

    custom = decide(
        definition,
        gallery.session,
        evt(ev.Answered, step_key="section:2", payload="custom_only"),
    )
    assert "image" in [section.key for section in screen(custom).components[0].sections]
    server = decide(
        definition,
        gallery.session,
        evt(ev.Answered, step_key="section:2", payload="server_blur"),
    )
    shown = [section.key for section in screen(server).components[0].sections]
    assert "image" not in shown


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


def test_a_required_multi_select_refuses_confirm_when_every_select_is_empty():
    """Broke as: default roles saved with both dropdowns empty."""
    definition, decision = start("default_roles")
    step = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    assert step.session.cursor == "default_roles_config"
    empty = decide(
        definition, step.session, evt(ev.Answered, step_key="default_roles_config")
    )
    assert kinds(empty) == ["ShowError"]
    assert empty.effects[0].key == "fill-one-field"
    assert empty.session.cursor == "default_roles_config"
    drafted = decide(
        definition,
        empty.session,
        evt(
            ev.Drafted,
            step_key="default_roles_config",
            changes={"default_roles": ["202"]},
        ),
    )
    one = decide(
        definition, drafted.session, evt(ev.Answered, step_key="default_roles_config")
    )
    assert one.session.cursor == "confirm"


def test_confirming_the_member_picker_on_the_review_does_not_save():
    """Broke as: Confirm on the member picker opened from the birthday review ran
    the review's Confirm and saved the whole setup."""
    definition = REGISTRY.get("reminders_birthday")
    session = (
        new_session(
            (definition.key, definition.version),
            Setup(),
            ORIGIN,
            ttl_seconds=60,
            now=NOW,
            answers={"reminders_birthday": Answer([{"user": Answer("777")}])},
        )
        .at("confirm")
        .with_status(Status.AWAITING, awaiting="edit")
    )
    picker = decide(
        definition, session, evt(ev.TargetChosen, value="reminders_birthday")
    )
    assert picker.session.awaiting == "member:edit"
    drafted = decide(
        definition,
        picker.session,
        evt(ev.Drafted, step_key="confirm", changes={"member": ["777"]}),
    )
    confirmed = decide(
        definition, drafted.session, evt(ev.Answered, step_key="confirm")
    )
    assert not [e for e in confirmed.effects if isinstance(e, Commit)]
    assert kinds(confirmed) == ["OpenChild"]


def test_a_card_modal_is_validated_with_the_other_items_and_the_lookup():
    """A card modal validated with its answers only: a streamer check that needs
    the Twitch lookup and the other items always refused."""
    from tests.forms.form.test_lookups import streamer_card

    definition = streamer_card()
    session = new_session(
        (definition.key, definition.version), Setup(), ORIGIN, ttl_seconds=60, now=NOW
    ).at("streamer_card")
    typed = evt(ev.Answered, step_key="section:0", payload={"inputs": ["@Shroud"]})

    missing = decide(
        definition, session, typed, Context(external={"twitch": {"user_id": None}})
    )
    assert kinds(missing) == ["ShowError"]
    assert missing.effects[0].key == "streamer-not-found"

    found = decide(
        definition, session, typed, Context(external={"twitch": {"user_id": "1"}})
    )
    assert found.session.answers["streamer_card"].part("streamer") == "shroud"

    taken = decide(
        definition,
        session,
        typed,
        Context(
            external={"twitch": {"user_id": "1"}},
            items=({"streamer": {"value": "shroud"}},),
        ),
    )
    assert taken.effects[0].key == "streamer-already-registered"


def _welcome_card_session(answers=None):
    from tests.forms.form.card_fixtures import welcome_like_card

    definition = welcome_like_card()
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers=answers,
    ).at("welcome_card")
    return definition, session


def test_a_modal_input_section_keeps_every_field_and_joins_unkeyed_ones():
    definition, session = _welcome_card_session()
    typed = evt(
        ev.Answered,
        step_key="section:3",
        payload={"inputs": ["Oi!", "Bem-vindo {user}", "Divirta-se", "Até mais"]},
    )

    changed = decide(definition, session, typed)

    state = changed.session.answers["welcome_card"].parts
    assert state["title"] == "Oi!"
    assert state["messages"] == "Bem-vindo {user};Divirta-se"
    assert state["footer"] == "Até mais"


def test_a_dependent_key_survives_a_change_its_validator_accepts():
    """Broke as: reset-on-change handed the validator the whole card state
    instead of the dependent value, so every validator but validate_date read a
    stringified mapping, found a space in it, and cleared the key on any change.
    Shared behaviour: the Check contract of app/settings/form/responses/
    validations.py. Consumer that exposed it: a card section whose reset rule
    names any validator other than validate_date. What must stay guaranteed: a
    reset rule clears its key only when the validator refuses that key's own
    value."""
    from tests.forms.form.card_fixtures import reset_on_change_card

    definition = reset_on_change_card()
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers={"mode": Answer("block"), "link": Answer("example.com")},
    ).at("site_card")

    changed = decide(
        definition, session, evt(ev.Answered, step_key="section:0", payload="allow")
    )

    state = changed.session.answers["site_card"].parts
    assert state["mode"] == "allow"
    assert state["link"] == "example.com"


def test_a_dependent_key_is_cleared_when_its_own_value_stops_being_valid():
    from tests.forms.form.card_fixtures import reset_on_change_card

    definition = reset_on_change_card()
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers={"mode": Answer("block"), "link": Answer("not a link")},
    ).at("site_card")

    changed = decide(
        definition, session, evt(ev.Answered, step_key="section:0", payload="allow")
    )

    assert changed.session.answers["site_card"].parts["link"] is None


def test_a_card_modal_asks_for_the_long_box_its_fields_declare():
    """Broke as: moving the message steps into card sections dropped their
    `multiline`, so a hundred character message typed in a one line box."""
    from app.settings.form.form_yaml import DiskSource, compile_form

    definition = compile_form("welcome_messages", DiskSource().load("welcome_messages"))
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
    ).at("welcome_config")
    card = definition.step("welcome_config")
    index = next(
        position
        for position, section in enumerate(card.sections)
        if section.key == "messages"
    )

    opened = decide(
        definition,
        session,
        evt(ev.SectionOpened, step_key="welcome_config", index=index),
    )

    inputs = screen(opened).components[0].inputs
    assert [one.multiline for one in inputs] == [True] * len(inputs)


def test_a_multi_field_modal_opens_with_the_saved_values():
    definition, session = _welcome_card_session(
        {"title": Answer("Oi!"), "messages": Answer("A;B"), "footer": Answer("F")}
    )

    opened = decide(
        definition, session, evt(ev.SectionOpened, step_key="welcome_card", index=3)
    )

    inputs = screen(opened).components[0].inputs
    assert [i.default for i in inputs] == ["Oi!", "A", "B", "F"]


def test_a_design_section_opens_the_gallery_and_keeps_the_chosen_design():
    definition, session = _welcome_card_session()

    opened = decide(
        definition, session, evt(ev.SectionOpened, step_key="welcome_card", index=1)
    )
    gallery = screen(opened).components[0]
    assert isinstance(gallery, Gallery)
    assert [d.key for d in gallery.designs] == ["server_blur", "custom_only"]
    assert [b.action for b in screen(opened).buttons] == ["picker_back"]
    assert opened.session.awaiting == "section:1"

    chosen = decide(
        definition,
        opened.session,
        evt(ev.Answered, step_key="section:1", payload="custom_only"),
    )
    assert chosen.session.answers["welcome_card"].part("design") == "custom_only"
    refused = decide(
        definition,
        chosen.session.with_status(Status.ACTIVE, awaiting="section:1"),
        evt(ev.Answered, step_key="section:1", payload="nope"),
    )
    assert refused.session.answers["welcome_card"].part("design") == "custom_only"


def test_a_file_upload_section_without_a_mode_counts_as_set_once_it_has_an_image():
    definition, session = _welcome_card_session({"channel": Answer("100")})
    custom = decide(
        definition,
        session.with_status(Status.ACTIVE, awaiting="section:1"),
        evt(ev.Answered, step_key="section:1", payload="custom_only"),
    )

    missing = decide(
        definition, custom.session, evt(ev.Answered, step_key="welcome_card")
    )
    assert kinds(missing) == ["ShowError"]

    uploaded = decide(
        definition,
        custom.session,
        evt(ev.Answered, step_key="section:2", payload={"url": "https://x/i.png"}),
    )
    state = uploaded.session.answers["welcome_card"].parts
    assert state["image"] == "https://x/i.png" and "" not in state
    done = decide(
        definition, uploaded.session, evt(ev.Answered, step_key="welcome_card")
    )
    assert done.session.answers["image"].raw == "https://x/i.png"


def test_a_part_edit_from_the_review_returns_to_the_review():
    from tests.forms.form.card_fixtures import welcome_like_card

    definition = welcome_like_card(edit_by_field=True)
    answers = {
        "channel": Answer("100"),
        "design": Answer("server_blur"),
        "title": Answer("Oi!"),
        "messages": Answer("A;B"),
        "footer": Answer("F"),
    }
    review = new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers=answers,
    ).at("confirm")
    opened, started = open_part(definition, review, Context(), "welcome_card/channel")
    chosen = decide(
        definition,
        started.session,
        evt(ev.Drafted, step_key="welcome_card", changes={"channel": ["101"]}),
    )
    handed = next(e for e in chosen.effects if isinstance(e, ResumeParent))
    back = decide(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="edit", answers=handed.answers),
    )
    assert back.session.cursor == "confirm" and back.session.raw("channel") == "101"
    assert not [e for e in back.effects if isinstance(e, Commit)]


def test_the_review_is_a_card_with_the_panel_groups_and_its_buttons_below():
    definition, decision = start("notifications_twitch")
    opened = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    item = {
        "channel": Answer("100"),
        "streamer": Answer("gaules"),
        "notification_messages": Answer("hi;hello"),
    }
    back = decide(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="add_item", answers=item),
    )

    review = screen(back)
    assert review.flavour == "components_v2"
    panel = review.components[0]
    assert isinstance(panel, Panel)
    lines = [line for group in panel.groups for line in group.lines]
    assert any("gaules" in line for line in lines)
    assert [b.action for b in review.buttons] == [
        "add",
        "aside:preview",
        "confirm",
        "cancel",
    ]


def test_the_review_description_resolves_response_tokens():
    import copy

    from app.settings.form.form_yaml import DiskSource, compile_form

    raw = copy.deepcopy(DiskSource().load("stream_elements_commands"))
    raw["steps"][-1].pop("description-when", None)
    raw["steps"][-1]["description"] = {
        "en-us": "Commands of {response:streamer|nobody}",
        "pt-br": "Comandos de {response:streamer|ninguém}",
    }
    definition = compile_form("stream_elements_commands", raw)
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers={"streamer": Answer("shroud")},
    ).at("confirm")

    drawn = decide(definition, session, evt(ev.ScreenRequested))

    assert screen(drawn).components[0].intro == "Comandos de shroud"


def test_edit_beside_a_review_group_returns_to_the_review():
    definition = REGISTRY.get("stream_elements_commands")
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers={"streamer": Answer("shroud")},
    ).at("confirm")
    review = decide(definition, session, evt(ev.ScreenRequested))
    [group] = screen(review).components[0].groups

    opened = decide(definition, review.session, evt(ev.EditRequested, target=group.key))
    assert opened.effects[0].session.mode == Edit(("streamer",))
    back = decide(
        definition,
        opened.session,
        evt(
            ev.ChildFinished, child_mode="edit", answers={"streamer": Answer("gaules")}
        ),
    )

    assert back.session.cursor == "confirm" and back.session.raw("streamer") == "gaules"
    assert isinstance(screen(back).components[0], Panel)


def _stream_elements_counting():
    import copy

    from app.settings.form.form_yaml import DiskSource, compile_form

    raw = copy.deepcopy(DiskSource().load("stream_elements_commands"))
    modal = next(step for step in raw["steps"] if step.get("key") == "streamer")
    modal["lookup_answers"] = {
        "stream_elements_commands_count": "stream_elements.enabled_commands",
        "stream_elements_top_commands": "stream_elements.top_commands",
    }
    raw["steps"][-1]["description"] = {
        "en-us": "{response:stream_elements_commands_count|0} commands",
        "pt-br": "{response:stream_elements_commands_count|0} comandos",
    }
    raw["steps"][-1].pop("description-when", None)
    return compile_form("stream_elements_commands", raw)


def _typed_streamer(definition):
    context = Context(
        external={
            "twitch": {"user_id": "1"},
            "stream_elements": {"enabled_commands": 12},
        }
    )
    session = new_session(
        (definition.key, definition.version), Setup(), ORIGIN, ttl_seconds=60, now=NOW
    ).at("streamer")
    typed = evt(ev.Answered, step_key="streamer", payload={"inputs": ["shroud"]})
    return decide(definition, session, typed, context)


def test_a_card_nobody_touched_yet_still_shows_its_defaults():
    """Broke as: Preview answered nothing when pressed right after the welcome
    card opened. Until the first change a card has no draft at all, so the
    session held none of the values the admin was reading on the screen."""
    from app.settings.form.form import shown_answers

    definition, opened = start("welcome_messages")
    answered = decide(
        definition,
        opened.session,
        evt(ev.Answered, step_key="form", payload=None),
        Context(),
    )

    shown = shown_answers(definition, answered.session, Context())

    state = shown["welcome_config"].parts
    assert state["welcome_messages_title"] == "Um novo membro chegou! 🎉"
    assert "bem-vindo ao" in str(state["welcome_messages"])
    assert not answered.session.answers.get("welcome_config"), "no draft yet"


def test_a_lookup_answer_is_kept_hidden_and_never_saved():
    """A looked up answer may be shown, as the review's own line, but it is
    never written to the document and never becomes a setting with an Edit."""
    definition = _stream_elements_counting()

    reviewed = _typed_streamer(definition)

    assert reviewed.session.raw("stream_elements_commands_count") == 12
    assert screen(reviewed).components[0].intro == "12 comandos"
    saved = to_document(definition.steps, reviewed.session.answers, "pt-br")
    assert "stream_elements_commands_count" not in saved
    groups = screen(reviewed).components[0].groups
    assert not any(
        "12" in line for group in groups if group.key for line in group.lines
    )


def test_the_review_lists_the_commands_it_found_under_the_streamer():
    """Lucas: the count belongs in a field of its own under the streamer, not
    buried in the paragraph above it, and the streamer's picture on the card."""
    definition, decision = start("stream_elements_commands")
    context = Context(
        external={
            "twitch": {
                "user_id": "1",
                "profile_image": "https://static-cdn.jtvnw.net/shroud.png",
            },
            "stream_elements": {
                "enabled_commands": 12,
                "top_commands": "`!mouse`, `!setup`",
            },
        }
    )
    typed = evt(ev.Answered, step_key="streamer", payload={"inputs": ["shroud"]})

    reviewed = decide(definition, decision.session.at("streamer"), typed, context)

    panel = screen(reviewed).components[0]
    lines = [line for group in panel.groups for line in group.lines]
    assert "12" not in panel.intro, "the count is a field, not a paragraph"
    assert any("Comandos ativos" in line and "12" in line for line in lines), lines
    assert any("`!mouse`, `!setup`" in line for line in lines), lines
    assert panel.thumbnail == "https://static-cdn.jtvnw.net/shroud.png"


def test_a_lookup_answer_belongs_to_the_step_that_looked_it_up():
    """Back forgets the answers its steps produce, so a lookup answer goes with
    the modal that ran the lookup."""
    from app.settings.form.form_yaml import produced_keys

    definition = _stream_elements_counting()

    assert "stream_elements_commands_count" in produced_keys(
        definition.step("streamer")
    )

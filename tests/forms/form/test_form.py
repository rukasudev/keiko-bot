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
)
from app.settings.form.form import decide
from app.settings.form.form_state import (
    AddItem,
    Answer,
    Edit,
    EditItem,
    Setup,
    Status,
    new_session,
)
from app.settings.form.form_yaml import DiskSource
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

BLOCK_LINKS_CARD = REGISTRY.get("block_links").step("link_settings")
BLOCK_LINKS_SECTIONS = [section.key for section in BLOCK_LINKS_CARD.sections]
EXCEPTIONS = BLOCK_LINKS_SECTIONS.index("exceptions")
CHANNEL_EXCEPTIONS = BLOCK_LINKS_SECTIONS.index("channel_exceptions")
OWN_LINKS = BLOCK_LINKS_SECTIONS.index("own_links")


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


@pytest.mark.parametrize("form", DiskSource().list())
def test_the_intro_keeps_one_blank_line_before_its_settings(form):
    """Broke as: StreamElements' intro showed two blank lines before its settings,
    because a YAML block text keeps its trailing newline and the intro appended the
    list after it."""
    _, decision = start(form)

    assert "\n\n\n" not in screen(decision).description


def test_answering_moves_to_the_next_allowed_step_and_skips_the_others():
    definition, decision = start("block_links")
    session = decision.session
    decision = decide(definition, session, evt(ev.Answered, step_key="form"))
    assert decision.session.cursor == "link_settings"
    assert isinstance(screen(decision).components[0], Card)
    decision = decide(
        definition, decision.session, evt(ev.Answered, step_key="link_settings")
    )
    assert decision.session.cursor == "confirm", "the card's own list is not walked"
    assert kinds(decision) == ["Render"]


def test_a_gate_answered_no_skips_the_list_it_gates():
    """Block links' add_custom gate is gone (bl-7); the gate contract runs on
    the fixture form with the same hidden gate before a list."""
    from tests.forms.form.gate_fixtures import gated_list

    definition = gated_list()
    session = new_session(
        (definition.key, definition.version), Setup(), ORIGIN, ttl_seconds=60
    )
    opened = decide(definition, session, evt(ev.Started))
    gate = decide(definition, opened.session, evt(ev.Answered, step_key="form"))
    assert gate.session.cursor == "add_now"
    decision = decide(
        definition, gate.session, evt(ev.Answered, step_key="add_now", payload=False)
    )
    assert decision.session.cursor == "confirm", "the list is skipped"
    assert any(
        rule.step_key == "things" and rule.skipped for rule in decision.evaluated_rules
    )


def test_a_draft_on_a_card_select_lands_in_the_card_and_keeps_the_card_on_screen():
    definition, decision = start("block_links")
    on_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    drafted = decide(
        definition,
        on_card.session,
        evt(ev.Drafted, step_key="link_settings", changes={"allowed_chats": ["100"]}),
    )
    assert kinds(drafted) == ["Render"]
    assert drafted.session.cursor == "link_settings"
    assert drafted.session.answers["link_settings"].part("allowed_chats") == "100"
    confirmed = decide(
        definition, drafted.session, evt(ev.Answered, step_key="link_settings")
    )
    assert confirmed.session.raw("allowed_chats") == "100"
    assert confirmed.session.cursor == "confirm"


def _to_review(form="block_links"):
    definition, decision = start(form)
    events = [
        evt(ev.Answered, step_key="form"),
        evt(ev.Answered, step_key="link_settings"),
    ]
    return definition, replay(definition, decision.session, events)[-1].session


def test_the_review_confirms_into_a_commit_and_completes_on_success():
    definition, session = _to_review()
    committing = decide(definition, session, evt(ev.Answered, step_key="confirm"))
    assert committing.session.status is Status.COMMITTING
    assert kinds(committing) == ["Commit"]
    done = decide(definition, committing.session, evt(ev.CommitSucceeded, kind="setup"))
    assert done.session.status is Status.COMPLETED
    assert kinds(done) == ["Finalize", "OpenPanel"]
    assert done.effects[0].kind == "enabled", "the activation embed, then the panel"
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
    assert "add_custom" not in document, "the gate left with bl-7"


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
    assert kinds(stale) == ["Render"]
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


def _gated_review():
    from tests.forms.form.gate_fixtures import gated_list

    definition = gated_list()
    session = new_session(
        (definition.key, definition.version), Setup(), ORIGIN, ttl_seconds=60
    )
    events = [
        evt(ev.Started),
        evt(ev.Answered, step_key="form"),
        evt(ev.Answered, step_key="add_now", payload=False),
    ]
    return definition, replay(definition, session, events)[-1].session


def test_back_keeps_the_target_answer_as_its_draft_and_the_earlier_ones():
    definition, session = _gated_review()
    back = decide(definition, session, evt(ev.Back))
    assert back.session.cursor == "add_now"
    assert back.session.raw("add_now") is False, "shown again as the draft"
    assert ("setup.step_back", {"step_key": "confirm"}) in back.analytics

    definition, session = _to_review()
    to_card = decide(definition, session, evt(ev.Back))
    assert to_card.session.cursor == "link_settings"
    assert to_card.session.raw("mode") == "block_all", "the card keeps every value"


def test_back_forgets_the_answers_of_the_steps_it_walks_over():
    definition, session = _gated_review()
    assert "things" in session.answers, "the review lists the skipped list"
    to_gate = decide(definition, session, evt(ev.Back))
    assert to_gate.session.raw("add_now") is False, "the target keeps its draft"
    assert "things" not in to_gate.session.answers, "walked over, forgotten"


def test_back_to_a_card_keeps_the_list_the_card_draws():
    """bl-7: the links added on the card belong to the card, so going back from
    the review to it keeps them."""
    definition, decision = start("block_links")
    link = {"link": Answer("meusite.com.br")}
    events = [
        evt(ev.Answered, step_key="form"),
        evt(ev.SectionOpened, step_key="link_settings", index=EXCEPTIONS),
        evt(ev.SectionOpened, step_key="link_settings", index=OWN_LINKS),
    ]
    opened = replay(definition, decision.session, events)[-1]
    assert kinds(opened) == ["OpenChild"], "the card's Add opens the link sub-form"
    back_on_card = decide(
        definition,
        opened.session,
        evt(ev.ChildFinished, child_mode="add_item", answers=link),
    )
    assert back_on_card.session.cursor == "link_settings", "the card, not the review"
    on_card = decide(definition, back_on_card.session, evt(ev.PickerClosed))
    review = decide(
        definition, on_card.session, evt(ev.Answered, step_key="link_settings")
    )
    back = decide(definition, review.session, evt(ev.Back))
    assert back.session.cursor == "link_settings"
    items = back.session.raw("custom_links")
    assert [item["link"].raw for item in items] == ["meusite.com.br"]


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
    assert kinds(refused) == ["Render", "ShowError"]
    assert refused.effects[-1].key == "streamer-not-found"
    assert refused.session.refused.error_key == "streamer-not-found"
    assert refused.session.cursor == "streamer"
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
    assert kinds(refused) == ["Render", "ShowError"]
    error = refused.effects[-1]
    assert error.plain, "a plain sentence on a message of its own (R3)"
    fields = error.args["fields"]
    assert "**Canal**" in fields and "**Horário da notificação**" in fields, fields
    assert not any(
        "🚨" in line for line in screen(refused).components[0].header.lines
    ), "the card itself shows no error line"
    missing = ("channel", "timezone", "notification_time")
    assert refused.session.refused.missing == missing
    assert (
        "setup.required_missing",
        {
            "card_key": "birthday_config",
            "missing_count": 3,
            "missing_keys": list(missing),
        },
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


def _exempt_section(session=None):
    definition, decision = start("block_links")
    at_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    card = definition.step("link_settings")
    index = [section.key for section in card.sections].index("exempt")
    opened = decide(
        definition,
        at_card.session,
        evt(ev.SectionOpened, step_key="link_settings", index=index),
    )
    return definition, index, opened


def test_a_selects_section_opens_one_screen_with_every_select():
    """bl-6: the channels and roles where Keiko never deletes links are one
    section of the card, and its button opens both selects together."""
    _definition, index, opened = _exempt_section()

    pickers = screen(opened).components
    assert [picker.slot for picker in pickers] == ["allowed_chats", "allowed_roles"]
    assert [picker.kind for picker in pickers] == ["channel", "role"]
    assert opened.session.awaiting == f"section:{index}"
    assert "**nunca**" in screen(opened).description


def test_picking_on_a_screen_of_several_selects_keeps_it_open_until_back():
    definition, index, opened = _exempt_section()

    picked = decide(
        definition,
        opened.session,
        evt(ev.Drafted, step_key="link_settings", changes={"allowed_chats": ["100"]}),
    )
    pickers = screen(picked).components
    assert [picker.selected for picker in pickers] == [("100",), ()]
    roles = decide(
        definition,
        picked.session,
        evt(
            ev.Drafted, step_key="link_settings", changes={"allowed_roles": ["7", "8"]}
        ),
    )
    back = decide(definition, roles.session, evt(ev.PickerClosed))

    card = screen(back).components[0]
    assert isinstance(card, Card)
    group = next(section for section in card.sections if section.key == "exceptions")
    assert group.preview[:2] == (
        "> #️⃣ **Canais permitidos:** <#100>",
        "> 🏷️ **Cargos permitidos:** <@&7>, <@&8>",
    )
    state = back.session.answers["link_settings"].parts
    assert (state["allowed_chats"], state["allowed_roles"]) == ("100", ["7", "8"])


@pytest.mark.parametrize(
    "mode,group,keys,held,custom",
    [
        (
            "block_all",
            EXCEPTIONS,
            ["mode", "answer", "exceptions"],
            ["exempt", "allowed_links", "own_links"],
            "📌 **Links personalizados permitidos**",
        ),
        (
            "allow_all",
            CHANNEL_EXCEPTIONS,
            ["mode", "answer", "channel_exceptions", "own_links"],
            ["exempt"],
            "📌 **Links personalizados bloqueados**",
        ),
    ],
)
def test_the_block_links_card_lists_what_it_lets_through_after_channels_and_roles(
    mode, group, keys, held, custom
):
    """C1 (beta.2 round 3): the popular websites sit under the channels and
    roles, everything Keiko lets through reads "permitidos", and the custom
    links heading follows the mode; round 5 E draws the exceptions on their own
    screen, and in "Bloquear alguns" the blocked links stay on the card."""
    definition, decision = start("block_links")
    at_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    drafted = decide(
        definition,
        at_card.session,
        evt(ev.Drafted, step_key="link_settings", changes={"mode": mode}),
    )
    opened = decide(
        definition,
        drafted.session,
        evt(ev.SectionOpened, step_key="link_settings", index=group),
    )

    card = screen(drafted).components[0]
    assert [section.key for section in card.sections] == keys
    exceptions = screen(opened).components[0]
    assert [section.key for section in exceptions.sections] == held
    headings = {
        section.key: section.heading
        for section in (*card.sections, *exceptions.sections)
    }
    assert headings["exempt"] == "🔓 **Canais e cargos permitidos**"
    assert headings["own_links"] == custom
    exempt = next(section for section in exceptions.sections if section.key == "exempt")
    assert exempt.preview == (
        "> #️⃣ **Canais permitidos:** Em branco",
        "> 🏷️ **Cargos permitidos:** Em branco",
    )


def test_the_exempt_channels_and_roles_are_saved_from_the_card():
    definition, index, opened = _exempt_section()
    picked = decide(
        definition,
        opened.session,
        evt(ev.Drafted, step_key="link_settings", changes={"allowed_roles": ["7"]}),
    )
    done = decide(
        definition, picked.session, evt(ev.Answered, step_key="link_settings")
    )

    document = to_document(definition.steps, done.session.answers, "pt-br")
    assert document["allowed_roles"] == {"style": "role", "values": "7"}
    assert document["allowed_chats"] == {"style": "channel", "values": []}
    assert "permissions" not in [step.key for step in definition.steps]


def _exceptions_screen(mode="block_all"):
    definition, decision = start("block_links")
    at_card = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    drafted = decide(
        definition,
        at_card.session,
        evt(ev.Drafted, step_key="link_settings", changes={"mode": mode}),
    )
    group = EXCEPTIONS if mode == "block_all" else CHANNEL_EXCEPTIONS
    opened = decide(
        definition,
        drafted.session,
        evt(ev.SectionOpened, step_key="link_settings", index=group),
    )
    return definition, opened


def _keys(decision):
    return [section.key for section in screen(decision).components[0].sections]


def test_configure_opens_the_exceptions_on_a_screen_of_their_own():
    """Round 5 E (v05 to v07): "Configurar" draws, on the same message, only the
    sections its group holds, with one Voltar below them and nothing else."""
    _definition, opened = _exceptions_screen()

    exceptions = screen(opened)
    card = exceptions.components[0]
    assert kinds(opened) == ["Render"]
    assert opened.session.opened_group == "exceptions"
    assert card.header.title == "🛡️ Exceções"
    assert card.header.description == (
        "Canais, cargos, sites e links que eu nunca apago."
    )
    assert _keys(opened) == ["exempt", "allowed_links", "own_links"]
    assert [(button.label, button.action) for button in exceptions.buttons] == [
        ("Voltar", "picker_back")
    ]


def test_a_choice_on_the_exceptions_screen_stays_there_and_back_shows_it_on_the_card():
    """Round 5 E: a pick on the exceptions screen is a draft of the card, the
    screen stays, and Voltar draws the card with the picks in the group's lines."""
    definition, opened = _exceptions_screen()
    channel = decide(
        definition,
        opened.session,
        evt(ev.Drafted, step_key="link_settings", changes={"allowed_chats": ["100"]}),
    )
    site = decide(
        definition,
        channel.session,
        evt(
            ev.Drafted,
            step_key="link_settings",
            changes={"allowed_links": ["youtube.com"]},
        ),
    )
    assert _keys(site) == ["exempt", "allowed_links", "own_links"]

    back = decide(definition, site.session, evt(ev.PickerClosed))

    assert back.session.opened_group is None
    assert _keys(back) == ["mode", "answer", "exceptions"]
    group = screen(back).components[0].sections[2]
    assert group.heading == "🛡️ **Adicionar exceções?**"
    assert group.description == "Canais, cargos, sites e links que eu nunca apago."
    assert group.preview == (
        "> #️⃣ **Canais permitidos:** <#100>",
        "> 🏷️ **Cargos permitidos:** Em branco",
        "> 🔗 **Sites populares permitidos:** Youtube",
        "> 📌 **Links personalizados permitidos:** Em branco",
    )
    assert [button.label for button in group.buttons] == ["Configurar"]
    state = back.session.answers["link_settings"].parts
    assert (state["allowed_chats"], state["allowed_links"]) == ("100", ["youtube.com"])


def test_in_block_some_the_exceptions_are_the_channels_and_roles_only():
    """Round 5 E: in "Bloquear alguns" the group tells only channels and roles,
    and its screen holds only them; the blocked links stay on the card."""
    definition, opened = _exceptions_screen("allow_all")
    assert _keys(opened) == ["exempt"]

    back = decide(definition, opened.session, evt(ev.PickerClosed))

    assert _keys(back) == ["mode", "answer", "channel_exceptions", "own_links"]
    group = screen(back).components[0].sections[2]
    assert group.description == "Canais e cargos onde eu nunca apago links."
    assert group.preview == (
        "> #️⃣ **Canais permitidos:** Em branco",
        "> 🏷️ **Cargos permitidos:** Em branco",
    )


def test_a_link_added_or_removed_on_the_exceptions_screen_comes_back_to_it():
    """Round 5 E: the list's Add opens its sub-form and Remove its picker, and
    each comes back to the exceptions screen, not to the card."""
    definition, opened = _exceptions_screen()
    link = {"link": Answer("meusite.com.br")}
    adding = decide(
        definition,
        opened.session,
        evt(ev.SectionOpened, step_key="link_settings", index=OWN_LINKS),
    )
    assert kinds(adding) == ["OpenChild"]
    added = decide(
        definition,
        adding.session,
        evt(ev.ChildFinished, child_mode="add_item", answers=link),
    )
    assert _keys(added) == ["exempt", "allowed_links", "own_links"]

    asked = decide(definition, added.session, evt(ev.RemoveRequested))
    kept = decide(definition, asked.session, evt(ev.PickerClosed))
    assert _keys(kept) == ["exempt", "allowed_links", "own_links"]
    asked = decide(definition, kept.session, evt(ev.RemoveRequested))
    chosen = decide(
        definition, asked.session, evt(ev.TargetsChosen, values=("custom_links$0",))
    )
    removed = decide(definition, chosen.session, evt(ev.RemovalConfirmed))

    assert _keys(removed) == ["exempt", "allowed_links", "own_links"]
    assert removed.session.raw("custom_links") == ()


def test_the_card_draws_its_group_again_after_back_and_done_closes_it():
    """Round 5 E: once Voltar closed the exceptions, Done saves what they hold,
    and the next screen opens with no group open."""
    definition, opened = _exceptions_screen()
    picked = decide(
        definition,
        opened.session,
        evt(ev.Drafted, step_key="link_settings", changes={"allowed_roles": ["7"]}),
    )
    back = decide(definition, picked.session, evt(ev.PickerClosed))
    done = decide(definition, back.session, evt(ev.Answered, step_key="link_settings"))

    assert done.session.cursor == "confirm"
    assert done.session.opened_group is None
    document = to_document(definition.steps, done.session.answers, "pt-br")
    assert document["allowed_roles"] == {"style": "role", "values": "7"}


def _member_card():
    definition = REGISTRY.get("reminders_birthday")
    child = new_session(
        (definition.key, 1),
        AddItem(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        parent_id="p",
    )
    return definition, decide(definition, child, evt(ev.Started))


def _drafted(definition, decision, **changes):
    return decide(
        definition,
        decision.session,
        evt(ev.Drafted, step_key="birthday_member_config", changes=changes),
    )


def _day_section(definition):
    return next(
        index
        for index, section in enumerate(definition.composition.steps[0].sections)
        if section.key == "day"
    )


def _typed_day(definition, decision, day):
    return decide(
        definition,
        decision.session,
        evt(
            ev.Answered,
            step_key=f"section:{_day_section(definition)}",
            payload={"inputs": [day]},
        ),
    )


def test_card_done_stores_the_transformed_date_and_the_fields():
    """B3 (beta.2 round 3): the member is chosen on the card itself, so an
    item's sub-form is the card alone."""
    definition, started = _member_card()
    assert started.session.cursor == "birthday_member_config"
    member = _drafted(definition, started, user=["777"])
    month = _drafted(definition, member, month=["05"])
    day = _typed_day(definition, month, "12")
    done = decide(
        definition, day.session, evt(ev.Answered, step_key="birthday_member_config")
    )
    assert done.session.status is Status.COMPLETED
    handed = done.effects[0]
    assert isinstance(handed, ResumeParent)
    assert handed.answers["date"].raw == "05-12"
    assert handed.answers["user"].raw == "777"


def test_an_invalid_day_is_refused_inside_the_card():
    """B1 (beta.2 round 4): the day is typed in its modal again, and a day the
    month does not have is still refused, on a message of its own."""
    definition, started = _member_card()
    month = _drafted(definition, started, month=["02"])
    refused = _typed_day(definition, month, "31")

    assert kinds(refused) == ["Render", "ShowError"]
    assert refused.effects[-1].key == "invalid-date"
    target = f"section:{_day_section(definition)}"
    assert refused.session.refused_on(target).error_key == "invalid-date"
    assert refused.session.answers["birthday_member_config"].part("day") is None


def test_a_day_typed_before_the_month_waits_for_the_month():
    """B1 (beta.2 round 4): with no month yet, a day some month has is kept and
    a month it does not fit clears it (`reset-on-change`); a text that is no
    day of any month is refused at once."""
    definition, started = _member_card()
    day = _typed_day(definition, started, "31")
    assert kinds(day) == ["Render"]
    month = _drafted(definition, day, month=["02"])
    assert month.session.answers["birthday_member_config"].part("day") is None

    for typed in ("32", "ab", "0"):
        refused = _typed_day(definition, started, typed)
        assert kinds(refused) == ["Render", "ShowError"], typed
        assert refused.effects[-1].key == "invalid-date"


def test_an_item_edit_never_changes_the_member_it_belongs_to():
    """Round 3 B: on a list whose `unique_by` is `unique_locked`, an edit draws
    the member as a line, no select, and a pick that still reaches it (an old
    message) changes nothing."""
    definition = REGISTRY.get("reminders_birthday")
    child = new_session(
        (definition.key, 1),
        EditItem(0),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        parent_id="p",
        answers={"user": Answer("777"), "date": Answer("05-12")},
    )
    started = decide(definition, child, evt(ev.Started))
    [card] = screen(started).components
    member = next(view for view in card.sections if view.key == "member")
    assert (member.preview, member.controls, member.buttons) == (("> <@777>",), (), ())

    picked = _drafted(definition, started, user=["555"])
    assert picked.session.answers["birthday_member_config"].part("user") == "777"


def _twitch_card():
    definition = REGISTRY.get("notifications_twitch")
    child = new_session(
        (definition.key, 1), AddItem(), ORIGIN, ttl_seconds=60, now=NOW, parent_id="p"
    )
    return definition, decide(definition, child, evt(ev.Started)).session


def test_a_card_with_a_preview_leads_its_buttons_with_it():
    """P1 (beta.2): Preview comes first, then Done, Back when there is one, Cancel."""
    definition = REGISTRY.get("notifications_twitch")
    child = new_session(
        (definition.key, 1), AddItem(), ORIGIN, ttl_seconds=60, now=NOW, parent_id="p"
    )
    item = decide(definition, child, evt(ev.Started))
    welcome_definition, opened = start("welcome_messages")
    welcome = decide(
        welcome_definition, opened.session, evt(ev.Answered, step_key="form")
    )

    assert [b.action for b in screen(item).buttons] == [
        "aside:preview",
        "done",
        "cancel",
    ]
    assert [b.action for b in screen(welcome).buttons] == [
        "aside:preview",
        "done",
        "cancel",
    ]


def test_a_preview_waits_for_the_keys_its_card_declares_it_needs():
    """H (round 6): Preview with no streamer answers the declared error alone."""
    definition, at_card = _twitch_card()
    empty = decide(definition, at_card, evt(ev.Aside, name="preview"))
    typed = at_card.with_answer(
        "notification", Answer(None, {"channel": "1", "streamer": "gaules"})
    )
    filled = decide(definition, typed, evt(ev.Aside, name="preview"))
    listed = decide(
        definition, at_card, evt(ev.Aside, name="preview", target="notifications$0")
    )

    assert kinds(empty) == ["ShowError"]
    assert (empty.effects[0].key, empty.effects[0].plain) == (
        "preview-needs-streamer",
        True,
    )
    assert empty.session.answers == at_card.answers
    assert kinds(filled) == ["RunAside"]
    assert kinds(listed) == ["RunAside"], "an item of a list is always complete"


def test_a_preview_with_nothing_declared_always_runs():
    welcome_definition, opened = start("welcome_messages")
    card = decide(welcome_definition, opened.session, evt(ev.Answered, step_key="form"))

    aside = decide(welcome_definition, card.session, evt(ev.Aside, name="preview"))

    assert kinds(aside) == ["RunAside"]


def test_the_variables_are_explained_in_the_messages_section_of_the_card():
    """P2 (beta.2): the variables sit with the messages they fill, not on top."""
    definition, at_card = _twitch_card()
    card = screen(decide(definition, at_card, evt(ev.Started))).components[0]
    messages = next(section for section in card.sections if section.key == "messages")

    assert "{stream_link}" not in card.header.description
    assert "`{streamer}`" in messages.description
    assert "`{stream_link}`" in messages.description
    assert all(
        not section.description for section in card.sections if section is not messages
    )


def test_the_welcome_card_explains_its_variables_on_top():
    """R6 (beta.2 round 2): on the welcome card the variables read on top of
    the card, under what to do, since under the messages they were missed."""
    definition = REGISTRY.get("welcome_messages")
    _, started = start("welcome_messages")
    at_card = decide(definition, started.session, evt(ev.Answered, step_key="form"))
    card = screen(at_card).components[0]

    assert "`{user}`" in card.header.description
    assert "`{member_count}`" in card.header.description
    assert all(not section.description for section in card.sections)


def test_the_card_keeps_keiko_on_top_and_the_streamer_picture_under_its_name():
    """P3 (beta.2, tw-1, tw-2): the header picture is always Keiko's; the
    streamer's picture shows under the streamer section once it is found."""
    from app.constants import KeikoIcons

    definition, at_card = _twitch_card()
    before = screen(decide(definition, at_card, evt(ev.Started))).components[0]
    typed = decide(
        definition,
        at_card,
        evt(ev.Answered, step_key="section:1", payload={"inputs": ["gaules"]}),
        Context(
            external={"twitch": {"user_id": "1", "profile_image": "https://g.png"}}
        ),
    )
    after = screen(typed).components[0]

    def streamer(card):
        return next(section for section in card.sections if section.key == "streamer")

    assert before.header.thumbnail == after.header.thumbnail == KeikoIcons.IMAGE_01
    assert streamer(before).media is None
    assert streamer(after).media == "https://g.png"


def _typed(definition, session, index, inputs, found=None):
    return decide(
        definition,
        session,
        evt(ev.Answered, step_key=f"section:{index}", payload={"inputs": inputs}),
        Context(external=found or {}),
    ).session


def _messages(session):
    return session.answers["notification"].part("notification_messages")


TWITCH_FOUND = {"twitch": {"user_id": "1", "profile_image": ""}}


def test_the_chosen_streamer_is_written_into_the_suggested_message():
    """tw-5 (beta.2): the suggested message comes with the name and the link."""
    definition, at_card = _twitch_card()

    typed = _typed(definition, at_card, 1, ["@Gaules"], TWITCH_FOUND)

    assert _messages(typed) == (
        "@everyone gaules está ao vivo! https://twitch.tv/gaules"
    )


def test_a_new_streamer_replaces_the_old_name_and_link_in_the_messages():
    """tw-5: the old name and link go, whatever their case, and only as words."""
    definition, at_card = _twitch_card()
    first = _typed(definition, at_card, 1, ["gaules"], TWITCH_FOUND)
    written = _typed(
        definition,
        first,
        2,
        ["Bora GAULES! https://twitch.tv/gaules", "o gaulesfan fica", "{streamer}"],
    )

    changed = _typed(definition, written, 1, ["cellbit"], TWITCH_FOUND)

    assert _messages(changed) == (
        "Bora cellbit! https://twitch.tv/cellbit;o gaulesfan fica;cellbit"
    )


def test_the_chosen_youtuber_is_written_in_and_the_video_link_stays_a_variable():
    """yt-3 (beta.2): only the channel name is known before the video exists."""
    definition = REGISTRY.get("notifications_youtube_video")
    child = new_session(
        (definition.key, 1), AddItem(), ORIGIN, ttl_seconds=60, now=NOW, parent_id="p"
    )
    at_card = decide(definition, child, evt(ev.Started)).session
    written = _typed(definition, at_card, 2, ["{youtuber} postou: {video_link}"])
    found = {"youtube": {"channel_id": "UC1", "profile_image": ""}}

    typed = _typed(definition, written, 1, ["pewdiepie"], found)

    assert _messages(typed) == "pewdiepie postou: {video_link}"


def test_an_unknown_streamer_is_told_apart_and_its_modal_keeps_what_was_typed():
    """Broke as (tw-10, PR8 #33): the card lost what the admin typed, and the
    analytics said required fields were missing when a validator had refused
    the value. Since beta.2 round 2 (R3) the error is a message of its own
    again, never a line inside the card."""
    definition, at_card = _twitch_card()
    refused = decide(
        definition,
        at_card,
        evt(ev.Answered, step_key="section:1", payload={"inputs": ["naoexiste"]}),
        Context(external={"twitch": {"user_id": None}}),
    )

    names = [name for name, _ in refused.analytics]
    assert "setup.required_missing" not in names, refused.analytics
    assert (
        "setup.validation_failed",
        {
            "step_key": "notification",
            "validation": "streamer-not-found",
            "error_key": "streamer-not-found",
        },
    ) in refused.analytics
    assert kinds(refused) == ["Render", "ShowError"], kinds(refused)
    assert refused.effects[-1].key == "streamer-not-found"
    card = screen(refused).components[0]
    lines = "\n".join(next(s for s in card.sections if s.index == 1).preview)
    assert "não consegui encontrar o streamer" not in lines, lines
    assert refused.session.refused.inputs == ("naoexiste",)

    reopened = decide(
        definition,
        refused.session,
        evt(ev.SectionOpened, step_key="notification", index=1),
    )
    modal = screen(reopened).components[0]
    assert modal.inputs[0].default == "naoexiste", "the modal keeps what was typed"


def test_a_refused_text_step_draws_a_card_with_what_was_typed_and_tells_why_apart():
    definition = REGISTRY.get("stream_elements_commands")
    _, decision = start("stream_elements_commands")
    at_modal = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    refused = decide(
        definition,
        at_modal.session,
        evt(ev.Answered, step_key="streamer", payload={"inputs": ["nobody"]}),
        Context(external={"twitch": {"user_id": None}}),
    )

    assert kinds(refused) == ["Render", "ShowError"], kinds(refused)
    card = screen(refused).components[0]
    assert isinstance(card, Card)
    lines = "\n".join(card.sections[0].preview)
    assert "nobody" in lines and "🚨" not in lines, lines
    again = decide(
        definition,
        refused.session,
        evt(ev.SectionOpened, step_key="streamer", index=0),
    )
    assert kinds(again) == ["OpenModal"]
    assert screen(again).components[0].inputs[0].default == "nobody"


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
    assert [b.action for b in review.buttons] == ["confirm", "cancel"]
    assert _card_actions(review) == [
        "add:notifications",
        "edit:notifications$0",
        "aside:preview:notifications$0",
    ]
    lines = [line for group in review.components[0].groups for line in group.lines]
    assert any("gaules" in line for line in lines)


def _card_actions(review):
    """What the buttons inside the review's card report, in the order drawn."""
    found = []
    for group in review.components[0].groups:
        for part in group.parts:
            found.append(part.button)
            found += [button.action for button in part.actions]
        found += [button.action for button in group.actions]
    return found


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
    assert "remove:notifications" not in _card_actions(screen(one))
    assert [b.action for b in screen(two).buttons] == ["confirm", "cancel"]
    assert _card_actions(screen(two))[-1] == "remove:notifications"
    picker = decide(definition, two.session, evt(ev.RemoveRequested))
    select = screen(picker).components[0]
    assert [option.label for option in select.options] == ["gaules", "cellbit"]
    assert select.max_values == 1, "the list keeps the one streamer it needs"
    chosen = decide(
        definition, picker.session, evt(ev.TargetsChosen, values=("notifications$0",))
    )
    assert "- gaules" in screen(chosen).description
    assert [b.action for b in screen(chosen).buttons] == ["remove_items", "picker_back"]
    removed = decide(definition, chosen.session, evt(ev.RemovalConfirmed))
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
    from tests.forms.form.gate_fixtures import gated_list

    definition = gated_list()
    session = new_session(
        (definition.key, definition.version), Setup(), ORIGIN, ttl_seconds=60
    )
    opened = decide(definition, session, evt(ev.Started))
    gate = decide(definition, opened.session, evt(ev.Answered, step_key="form"))
    assert isinstance(screen(gate).components[0], Choice)
    yes = decide(
        definition,
        gate.session,
        evt(ev.Answered, step_key="add_now", payload="True"),
    )
    assert yes.session.raw("add_now") is True
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


def test_a_card_that_needs_one_of_its_selects_refuses_done_when_every_one_is_empty():
    """Broke as: default roles saved with both dropdowns empty. Since beta.2
    (dr-2) the roles are a card, and `required_any` keeps that refusal: Done
    stays on the card and says, on a message of its own, that one is needed."""
    definition, decision = start("default_roles")
    step = decide(definition, decision.session, evt(ev.Answered, step_key="form"))
    assert step.session.cursor == "default_roles_config"
    empty = decide(
        definition, step.session, evt(ev.Answered, step_key="default_roles_config")
    )
    assert kinds(empty) == ["Render", "ShowError"]
    assert empty.effects[-1].key == "fill-one-field"
    assert empty.session.refused.missing == ("default_roles_bot", "default_roles")
    header = screen(empty).components[0].header
    assert not any("🚨" in line for line in header.lines)
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
    assert one.session.cursor == "sync_existing"


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
    assert kinds(missing) == ["Render", "ShowError"]
    assert missing.session.refused_on("section:0").error_key == "streamer-not-found"

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
    assert taken.session.refused.error_key == "streamer-already-registered"


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
    assert kinds(missing) == ["Render", "ShowError"]
    assert missing.session.refused.missing == ("image",)

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
    assert any("```• hi\n• hello```" in line for line in lines), (
        "the messages read in a code block (R2)"
    )
    assert [b.action for b in review.buttons] == ["confirm", "cancel"]


def test_the_review_names_the_command_it_turns_on_and_closes_with_confirm():
    definition = REGISTRY.get("default_roles")
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        answers={"default_roles": Answer(["201"])},
    ).at("confirm")

    drawn = decide(
        definition,
        session,
        evt(ev.ScreenRequested),
        Context(command="moderações cargos padrão"),
    )

    review = screen(drawn)
    assert "`/moderações cargos padrão`" in review.components[0].intro
    assert review.components[0].title == "👩‍🎓 Cargos Padrão"
    confirm = review.buttons[-2]
    assert (confirm.label, confirm.emoji, confirm.action) == (
        "Confirmar",
        None,
        "confirm",
    ), "the review closes with a plain Confirmar (R1)"


def _review_of(form):
    definition = REGISTRY.get(form)
    session = new_session(
        (definition.key, definition.version), Setup(), ORIGIN, ttl_seconds=60, now=NOW
    ).at("confirm")
    drawn = decide(
        definition, session, evt(ev.ScreenRequested), Context(command="comando")
    )
    return screen(drawn).components[0]


def test_the_review_info_is_the_note_that_closes_it_not_its_description():
    """`info` on a review is the panel's closing note, drawn above the footer."""
    birthday = _review_of("reminders_birthday")
    roles = _review_of("default_roles")

    assert birthday.info.startswith("Ao confirmar no botão abaixo, você poderá criar")
    assert "Ao confirmar" not in birthday.intro
    assert birthday.intro.endswith("`/comando` com as configurações abaixo? ✅")
    assert roles.info == "", "a review without `info` closes with no note"


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
    modal.pop("media_key", None)
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
    buried in the paragraph above it; the streamer's picture sits under the
    streamer's line, and the card's own picture is Keiko's (P3)."""
    from app.constants import KeikoIcons

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
    streamer = next(group for group in panel.groups if group.key == "streamer")
    assert streamer.media == "https://static-cdn.jtvnw.net/shroud.png"
    assert panel.thumbnail == KeikoIcons.IMAGE_03, "the card picture is always Keiko's"


def test_a_lookup_answer_belongs_to_the_step_that_looked_it_up():
    """Back forgets the answers its steps produce, so a lookup answer goes with
    the modal that ran the lookup."""
    from app.settings.form.form_yaml import produced_keys

    definition = _stream_elements_counting()

    assert "stream_elements_commands_count" in produced_keys(
        definition.step("streamer")
    )


def _default_roles_card(context):
    definition, decision = start("default_roles", context=context)
    at_card = decide(
        definition, decision.session, evt(ev.Answered, step_key="form"), context
    )
    return definition, at_card


def _past_the_roles_card(context, roles=("202", "203")):
    definition, at_card = _default_roles_card(context)
    drafted = decide(
        definition,
        at_card.session,
        evt(
            ev.Drafted,
            step_key="default_roles_config",
            changes={"default_roles": list(roles)},
        ),
        context,
    )
    done = decide(
        definition,
        drafted.session,
        evt(ev.Answered, step_key="default_roles_config"),
        context,
    )
    return definition, done


def test_the_setup_asks_after_the_card_whether_to_give_the_roles_to_who_is_there():
    """N3 (beta.2 round 2): the card keeps only the roles; Next asks, on a step
    of its own, with the server's totals and the roles each group gets."""
    context = Context(values={"members_count": "12", "bots_count": "3"})
    definition, asked = _past_the_roles_card(context)

    assert asked.session.cursor == "sync_existing"
    shown = screen(asked)
    assert shown.title == "🔄 Dar os cargos para quem já está no servidor hoje?"
    assert shown.description.startswith(
        "Os seguintes membros atuais irão receber os cargos:\n\n👥 **Membros**\n"
    )
    assert (
        "- Quantidade: **12**\n"
        "- Cargos a serem atribuídos a esses membros: <@&202>, <@&203>"
        in shown.description
    )
    assert "🤖 **Bots**\n- Quantidade: **3**\n" in shown.description
    assert shown.description.endswith("Cargo a ser atribuído a esses bots: Em branco")
    choice = shown.components[0]
    assert [(option.label, option.style) for option in choice.options] == [
        ("Sim", "primary"),
        ("Não", "secondary"),
    ]
    assert choice.auto_confirm
    assert [button.action for button in shown.buttons] == ["back", "cancel"]


def test_the_roles_card_has_no_toggle_to_give_the_roles_now():
    definition, at_card = _default_roles_card(Context())

    shown = [section.key for section in screen(at_card).components[0].sections]
    assert shown == ["default_roles_bot", "default_roles"]


@pytest.mark.parametrize("pressed,wanted", [("True", True), ("False", False)])
def test_the_answer_on_the_roles_step_is_kept_for_the_commit(pressed, wanted):
    definition, asked = _past_the_roles_card(Context(), roles=("202",))
    answered = decide(
        definition,
        asked.session,
        evt(ev.Answered, step_key="sync_existing", payload=pressed),
    )

    assert answered.session.cursor == "confirm"
    assert answered.session.raw("sync_existing") is wanted
    assert answered.session.raw("default_roles") == "202"
    review = screen(answered).components[0]
    assert "sync_existing" not in str(review)


def _birthday_card(context):
    definition, decision = start("reminders_birthday", context=context)
    at_card = decide(
        definition, decision.session, evt(ev.Answered, step_key="form"), context
    )
    return definition, at_card


def test_the_birthday_card_picks_every_setting_right_on_it():
    """N4 (beta.2 round 2): every card draws its selects and buttons on itself;
    only a typed field keeps the button that opens its modal."""
    definition, at_card = _birthday_card(Context())
    actions = [button.action for button in screen(at_card).buttons]
    assert actions == ["done", "cancel"]

    sections = {
        section.key: section for section in screen(at_card).components[0].sections
    }
    assert [type(control) for control in sections["channel"].controls] == [Picker]
    assert sections["channel"].buttons == ()
    timezone = sections["timezone"].controls[0]
    assert timezone.slot == "timezone" and len(timezone.options) == 9
    time = sections["notification_time"].controls[0]
    assert time.slot == "notification_time" and len(time.options) == 8
    mention = sections["mention_everyone"].controls[0]
    assert [(option.label, option.style) for option in mention.options] == [
        ("Sim", "secondary"),
        ("Não", "primary"),
    ]
    assert mention.slot == "mention_everyone"
    assert sections["default_message"].controls == ()
    assert sections["default_message"].buttons, "a modal cannot sit on the card"

    picked = decide(
        definition,
        at_card.session,
        evt(
            ev.Drafted,
            step_key="birthday_config",
            changes={"notification_time": ["10:00"], "mention_everyone": "true"},
        ),
    )
    draft = picked.session.answers["birthday_config"]
    assert draft.part("notification_time") == "10:00"
    assert draft.part("mention_everyone") is True
    redrawn = {s.key: s for s in screen(picked).components[0].sections}
    chosen = [
        option.value
        for option in redrawn["notification_time"].controls[0].options
        if option.selected
    ]
    assert chosen == ["10:00"]
    styles = [
        option.style for option in redrawn["mention_everyone"].controls[0].options
    ]
    assert styles == ["primary", "secondary"]

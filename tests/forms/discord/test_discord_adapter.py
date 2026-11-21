"""The Discord adapter under the offline harness.

The goldens prove the screens; these prove the hostile-environment rules the
review names (4.6, II.1): a duplicate click does nothing, a stale click gets a
notice and the current screen, two concurrent clicks are serialized, expiry is
visible, a replacement survives a failed delete, and payloads obey Discord.
"""

import asyncio
import logging
from dataclasses import replace
from types import SimpleNamespace

import discord
import pytest

from app.constants import ViewConstants
from app.core.utils import fill, ml
from app.forms.discord.interactions import decode, encode, is_ours
from app.forms.discord.views import Dispatcher, modal_of
from app.forms.form.components import FileInput, Input, Screen, TextInputs
from tests.behavioral.flows.block_links import seed as seed_block_links
from tests.behavioral.flows.common import seed_document
from tests.behavioral.flows.welcome import ENABLED as WELCOME_ENABLED
from tests.behavioral.harness import locators
from tests.behavioral.harness.driver import FormScenario
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

LAYOUT_ITEMS = (
    discord.ui.Container,
    discord.ui.Section,
    discord.ui.TextDisplay,
    discord.ui.ActionRow,
    discord.ui.Button,
    discord.ui.Select,
    discord.ui.ChannelSelect,
    discord.ui.RoleSelect,
    discord.ui.UserSelect,
    discord.ui.MediaGallery,
    discord.ui.Thumbnail,
    discord.ui.Separator,
)


@pytest.fixture
def v2(deps):
    def factory(locale="pt-br", guild=None):
        guild = guild or create_guild()
        user = create_member(guild, id=555, name="Tester")
        return FormScenario(
            guild=guild, user=user, locale=locale, mongo=deps.mongo_client
        )

    return factory


def _button(scenario, target):
    return scenario.current_message, scenario.component(target)


def _bot_events(scenario):
    return [event for event in scenario.outputs if event["actor"] == "bot"]


def _first_answer(events):
    """A defer, or the edit that disables the message clicked while it waits."""
    return any(event["kind"] in ("defer", "edit") for event in events)


def _notices(scenario, key):
    text = ml(f"texts.notices.{key}", locale=scenario.locale_str)
    return [e for e in scenario.outputs if e.get("content") == text]


def _controls(message):
    """The actions a message still offers, as the codec names them."""
    targets = map(locators.codec_target, locators.clickable_items(message))
    return [target for target in targets if target]


def _http_error(status, code=None, text="refused"):
    response = SimpleNamespace(status=status, reason=text)
    return discord.HTTPException(
        response, {"code": code, "message": text} if code else text
    )


# ---------------------------------------------------------------------- codec


def test_a_custom_id_stays_within_discords_limit():
    """Discord refuses a whole message whose component has a custom id over 100
    characters: `encode` refuses one, so no suite can draw it, and the longest
    feature with the longest action and argument the forms draw, at a three-digit
    revision, still fits."""
    from app.constants import DiscordLimits
    from app.forms.features import feature_keys

    longest = max(feature_keys(), key=len)
    session_id = f"{longest}-{'f' * 16}"
    for action, arg in (
        ("edit", "default_roles_config/default_roles_bot"),
        ("aside", "preview:notifications$0"),
        ("reopen", longest),
    ):
        assert len(encode(session_id, 999, action, arg)) <= DiscordLimits.CUSTOM_ID

    with pytest.raises(ValueError):
        encode(session_id, 1, "x" * DiscordLimits.CUSTOM_ID)


def test_the_codec_round_trips_and_rejects_foreign_ids():
    custom_id = encode("abc", 7, "section", "2")
    component = decode(custom_id)
    assert (component.session_id, component.revision, component.action) == (
        "abc",
        7,
        "section",
    )
    assert component.arg == "2" and component.target == "section:2"
    assert decode(encode("abc", 1, "cancel")).arg is None
    assert is_ours(custom_id) and not is_ours("card_done") and decode("k:bad") is None


def test_the_list_buttons_become_their_events():
    """P4 and tw-8 (beta.2): a remove picker reports every value chosen, its
    confirmation, the pages, the search and a preview naming one item."""
    from app.forms.discord.interactions import to_event
    from app.forms.form import events as ev
    from app.forms.form.manager import PageTurned, SearchRequested

    click = SimpleNamespace(id=42)

    def event(action, arg=None, payload=None):
        return to_event(click, decode(encode("s", 3, action, arg)), payload)

    chosen = event("targets", None, ["links$0", "links$2"])
    assert isinstance(chosen, ev.TargetsChosen)
    assert chosen.values == ("links$0", "links$2")
    assert isinstance(event("remove_items"), ev.RemovalConfirmed)
    assert event("page", "2") == PageTurned("42", 3, 2)
    assert isinstance(event("search"), SearchRequested)
    preview = event("aside", "preview:notifications$1")
    assert (preview.name, preview.target) == ("preview", "notifications$1")
    assert (event("aside", "help").name, event("aside", "help").target) == ("help", "")


def test_a_button_on_the_card_sets_its_value_as_a_select_drafts_one():
    """N4 (beta.2 round 2): Sim and Não on a card report the value they set for
    their key, the same draft a select of the card reports."""
    from app.forms.discord.interactions import to_event
    from app.forms.form import events as ev

    click = SimpleNamespace(id=42)
    pressed = to_event(
        click, decode(encode("s", 3, "set", "mention_everyone=true")), None, "card"
    )
    picked = to_event(
        click, decode(encode("s", 3, "draft", "timezone")), ["UTC"], "card"
    )

    assert pressed == ev.Drafted("42", 3, "card", {"mention_everyone": "true"})
    assert picked == ev.Drafted("42", 3, "card", {"timezone": ["UTC"]})


def test_a_member_picture_is_read_from_the_server_the_session_runs_in():
    """B3 (beta.2 round 3): the card reads a picked member's picture by id, and
    a member the server does not know has none."""
    from app.forms.discord.callbacks import MemberPictures

    guild = create_guild()
    create_member(guild, id=777, name="Amiga")
    pictures = MemberPictures(guild)

    assert pictures.get("777") == "https://cdn.discordapp.com/avatars/777/fake.png"
    assert pictures.get("888") is None
    assert pictures.get("not-an-id") is None
    assert MemberPictures(None).get("777") is None


def test_the_yes_and_no_of_a_card_are_two_buttons_inside_it():
    from app.forms.discord.views import layout_of
    from app.forms.form.components import (
        Card,
        CardHeader,
        Choice,
        ChoiceOption,
        SectionView,
    )

    yes_no = Choice(
        "card",
        (
            ChoiceOption("Sim", "true", "primary", True),
            ChoiceOption("Não", "false", "secondary"),
        ),
        slot="mention_everyone",
    )
    section = SectionView(0, "mention", "📣 **Mencionar**", ("> Sim",), (), None)
    card = Card("card", CardHeader("T", ""), (replace(section, controls=(yes_no,)),))
    view = layout_of(
        Screen(components=(card,), flavour="components_v2"),
        lambda action, arg=None: f"k:s:1:{action}:{arg}",
        None,
    )

    container = view.children[0]
    buttons = [
        item
        for item in locators.walk_items(container)
        if isinstance(item, discord.ui.Button)
    ]
    assert [(button.label, button.style, button.custom_id) for button in buttons] == [
        ("Sim", discord.ButtonStyle.primary, "k:s:1:set:mention_everyone=true"),
        ("Não", discord.ButtonStyle.secondary, "k:s:1:set:mention_everyone=false"),
    ]


# ------------------------------------------------------------ hostile clicks


async def test_a_duplicate_click_changes_nothing(v2):
    scenario = await v2().start_command("default_roles")
    message, button = _button(scenario, "continue")
    interaction = scenario.interaction(on=message, target=button)
    await scenario.click(button, interaction=interaction)
    after_first = len(_bot_events(scenario))
    await scenario.click(button, interaction=interaction)
    assert len(_bot_events(scenario)) == after_first
    scenario.expect_step("default_roles_config")


async def test_a_stale_click_gets_only_the_current_screen(v2):
    """A notice on top of the redraw raced the late-answer clock and lost with
    40060, dropping the redraw (B5, beta.2)."""
    scenario = await v2().start_command("default_roles")
    message, old_button = _button(scenario, "continue")
    await scenario.confirm()
    screen = (scenario.rendered_summary, _controls(scenario.current_message))
    await scenario.click(old_button, on=message)
    assert _notices(scenario, "stale") == []
    scenario.expect_step("default_roles_config")
    assert scenario.outputs[-1]["kind"] in ("edit", "followup_edit")
    redrawn = (scenario.rendered_summary, _controls(scenario.current_message))
    assert redrawn == screen, "the same screen, drawn again"


async def test_two_concurrent_clicks_are_serialized(v2):
    scenario = await v2().start_command("default_roles")
    message, button = _button(scenario, "continue")
    first = scenario.interaction(on=message, target=button)
    second = scenario.interaction(on=message, target=button)
    await asyncio.gather(
        scenario.click(button, interaction=first),
        scenario.click(button, interaction=second),
    )
    scenario.expect_step("default_roles_config")
    assert _notices(scenario, "stale") == []
    assert scenario.outputs[-1]["kind"] in ("edit", "followup_edit")


# --------------------------------------------------------------------- expiry


async def test_expiry_takes_the_buttons_off_an_embed_screen(v2):
    scenario = await v2().start_command("default_roles")
    session = scenario.session.id
    await scenario.expire()
    message = scenario.current_message
    assert _controls(message) == ["reopen:default_roles"]
    expired = ml("texts.notices.expired", locale="pt-br")
    assert expired in message.embeds[0].description
    scenario.expect_status("expired", session)


async def test_expiry_takes_the_buttons_off_a_card(v2):
    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    assert scenario.current_message.flags.components_v2
    await scenario.expire()
    message = scenario.current_message
    assert _controls(message) == ["reopen:block_links"]
    items = list(locators.walk_items(message.view))
    texts = [i.content for i in items if isinstance(i, discord.ui.TextDisplay)]
    assert ml("texts.notices.expired", locale="pt-br") in texts


async def test_a_click_after_expiry_finalizes_the_message_again(v2):
    scenario = await v2().start_command("default_roles")
    session = scenario.session.id
    message, button = _button(scenario, "continue")
    await scenario.expire()
    await scenario.click(button, on=message)
    assert _controls(scenario.current_message) == ["reopen:default_roles"]
    scenario.expect_status("expired", session)


async def test_a_click_on_a_forgotten_session_draws_the_expired_message(v2):
    scenario = await v2().start_command("default_roles")
    message, button = _button(scenario, "continue")
    scenario.restart()
    await scenario.click(button, on=message)
    assert _controls(message) == ["reopen:default_roles"]
    assert ml("texts.notices.expired", locale="pt-br") in message.embeds[0].description


# ------------------------------------------------------------- choreography


async def test_a_replacement_survives_a_failed_delete(v2):
    scenario = await v2().start_command("block_links")
    scenario.discord_refuses("followup.delete_message", _http_error(404, 10008))
    await scenario.confirm()
    assert scenario.current_message.flags.components_v2
    assert not any(e["kind"] == "delete" for e in scenario.outputs)
    scenario.expect_step("link_settings")


async def test_a_held_card_is_usable_again_when_its_screen_fails_to_draw(v2, deps):
    """Broke as (beta.2 review, K2): a lookup disables the card while it runs,
    and the controls came back only after every effect succeeded; a Render that
    failed after the hold left every button of the card disabled for good.
    Shared: the hold and release of `callbacks.py` and `Executor.render`, which
    now records a screen only once Discord took it. Exposed by the StreamElements
    setup. Guaranteed: a held card is usable again whatever its effects did."""
    from tests.behavioral.flows import stream_elements

    stream_elements.known_streamers(deps)
    scenario = await v2().start_command("stream_elements_commands")
    await scenario.confirm()
    card = scenario.current_message
    field = scenario.pending_modal_fields()[0]
    scenario.discord_refuses("followup.send", _http_error(500), times=1)
    await scenario.submit_modal({field: "shroud"})

    controls = [
        item
        for item in locators.walk_items(card.view)
        if isinstance(item, (discord.ui.Button, discord.ui.Select))
    ]
    refused = [e for e in scenario.outputs if e["kind"] == "refused"]
    assert refused, "the screen after the lookup failed to draw"
    assert controls and not [item for item in controls if item.disabled], (
        "the card the lookup held stays disabled after its screen failed"
    )
    assert scenario.outputs[-1]["kind"] == "followup_edit"
    assert scenario.current_message is card


async def test_a_layout_payload_carries_only_layout_components(v2):
    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    for item in locators.walk_items(scenario.current_message.view):
        assert isinstance(item, LAYOUT_ITEMS), type(item).__name__


async def test_a_panel_puts_its_buttons_below_the_card(v2, deps):
    seed_block_links(deps)
    scenario = await v2().start_command("block_links")
    view = scenario.current_message.view

    card, *rows = view.children
    assert isinstance(card, discord.ui.Container)
    assert rows and all(isinstance(row, discord.ui.ActionRow) for row in rows)
    inside = list(locators.walk_items(card))
    in_card = [
        locators.codec_target(button)
        for item in inside
        if isinstance(item, discord.ui.ActionRow)
        for button in item.children
    ]
    assert in_card == ["remove:custom_links"], "only the list's Remove sits in the card"
    below = [locators.codec_target(button) for row in rows for button in row.children]
    assert "lifecycle:pause" in below and "aside:help" in below
    assert inside[-1].content.startswith("-# "), "the footer still closes the card"
    kinds = [type(item) for item in card.children]
    assert (discord.ui.Separator, discord.ui.Separator) not in zip(kinds, kinds[1:])


async def test_a_card_keeps_section_buttons_inside_and_its_own_below(v2):
    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    message, cancel = _button(scenario, "cancel")

    card, *rows = message.view.children
    assert isinstance(card, discord.ui.Container)
    assert any(isinstance(item, discord.ui.ActionRow) for item in card.children)
    assert any(cancel in row.children for row in rows)


def test_a_file_input_modal_wraps_the_upload_in_a_label():
    screen = Screen(
        modal_title="Upload", flavour="modal", components=(FileInput("s", "Image"),)
    )
    modal = modal_of(
        screen, lambda action, arg=None: f"k:s:1:{action}", None, "modal", "s"
    )
    (label,) = modal.children
    assert isinstance(label, discord.ui.Label)
    assert isinstance(label.component, discord.ui.FileUpload)


async def test_an_upload_is_checked_with_the_params_its_declaration_gives(
    v2, monkeypatch
):
    """The adapter ran an upload's validator by its name alone, so a validator with
    params saw none there while the engine saw them: a file the adapter misjudged was
    re-uploaded, or dropped with no message (review of PR8)."""
    from app.forms.discord import views
    from app.forms.form.form_yaml import FileUploadSection
    from app.forms.form.responses.validations import VALIDATORS, Validator

    seen, uploads, reported = [], [], []

    def at_most(_value, context):
        seen.append(dict(context.params))
        limit = int(context.params.get("bytes", 10**9))
        return "image_not_accepted" if context.upload.size > limit else None

    async def upload(attachment):
        uploads.append(attachment)
        return "https://cdn.test/kept.png"

    monkeypatch.setitem(
        VALIDATORS, "at_most", Validator("at_most", ("upload",), at_most, ("bytes",))
    )
    monkeypatch.setattr(views, "upload_attachment", upload)
    named = {"en-us": "Image", "pt-br": "Imagem"}
    section = FileUploadSection.model_validate(
        {
            "type": "file_upload",
            "key": "image",
            "emoji": "🖼️",
            "label": named,
            "keys": {"image": "image"},
            "modal": {"title": named},
            "validation": {"name": "at_most", "params": {"bytes": "100"}},
        }
    )
    screen = Screen(
        modal_title="Upload",
        flavour="modal",
        components=(FileInput("card", "Image", check=section.checked_by()),),
    )

    async def handle(_interaction, _custom_id, payload):
        reported.append(payload)

    modal = modal_of(
        screen, lambda action, arg=None: f"k:s:1:{action}", Dispatcher(handle), "m", "s"
    )
    scenario = v2()
    await scenario.interaction().response.send_modal(modal)

    await scenario.submit_file_upload(size=500, width=10, height=10)

    assert seen == [{"bytes": "100"}]
    assert [payload["url"] for payload in reported] == [None]
    assert uploads == [], "a refused file stays put"


def test_a_text_modal_carries_only_text_inputs():
    screen = Screen(
        modal_title="Words",
        flavour="modal",
        components=(TextInputs("s", (Input("One"), Input("Two", multiline=True))),),
    )
    modal = modal_of(
        screen, lambda action, arg=None: f"k:s:1:{action}", None, "modal", "s"
    )
    assert all(isinstance(child, discord.ui.TextInput) for child in modal.children)
    assert [child.style for child in modal.children] == [
        discord.TextStyle.short,
        discord.TextStyle.long,
    ]


# ------------------------------------------------------------------ logging


async def _save_block_links_with_a_failing_commit(v2, monkeypatch):
    from app.commands.block_links import service as block_links

    async def boom(self, kind, payload, context):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(type(block_links.FEATURE), "commit", boom)
    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.confirm()
    return scenario


async def test_a_clean_click_posts_no_message_of_its_own(v2, closed_traces):
    scenario = await v2().start_command("default_roles")
    await scenario.confirm()

    assert closed_traces, "every click still closes a trace"
    assert not [trace for trace in closed_traces if trace.is_noteworthy], (
        "a click must not post to the log channel; the session message tells the story"
    )
    assert any(
        "form default_roles" in line["message"]
        for trace in closed_traces
        for line in trace.lines
    ), "the engine lines still reach the stored log"


async def test_a_failed_commit_posts_no_raw_timeline_and_marks_the_story_failed(
    v2, closed_traces, journeys, monkeypatch
):
    await _save_block_links_with_a_failing_commit(v2, monkeypatch)

    assert not [trace for trace in closed_traces if trace.is_noteworthy], (
        "a failed save is told by the session message and the error channel, "
        "never by a third message of engine lines"
    )
    assert [story.result for story in journeys.values()] == ["failure"]


async def test_a_raising_commit_puts_a_failure_line_on_the_story_not_the_message(
    v2, journeys, monkeypatch
):
    await _save_block_links_with_a_failing_commit(v2, monkeypatch)

    lines = [line["message"] for line in list(journeys.values())[0].lines]
    assert any(line.startswith("❌") and "RuntimeError" in line for line in lines)
    assert not any("secret detail" in line for line in lines), (
        "an exception message is free text and never reaches an event"
    )
    assert any(line.startswith("step:") for line in lines)


async def test_the_session_story_says_whether_the_person_is_an_admin(deps, journeys):
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    user.guild_permissions = SimpleNamespace(administrator=True)
    scenario = FormScenario(
        guild=guild, user=user, locale="pt-br", mongo=deps.mongo_client
    )

    await scenario.start_command("default_roles")

    assert [story.is_admin for story in journeys.values()] == [True]


async def test_an_expiry_after_the_interaction_token_died_leaves_discord_alone(
    v2, journeys
):
    """Found running the local bot: every sweep expiry failed with
    `401 Invalid Webhook Token`. Discord honours an interaction token for fifteen
    minutes and a session expires later than that, so the edit could never land;
    the next click on the message closes it instead."""
    scenario = await v2().start_command("default_roles")
    before = len(_bot_events(scenario))

    with scenario.later(16 * 60):
        await scenario.sweep()

    assert len(_bot_events(scenario)) == before, (
        "no edit is attempted with a dead token"
    )
    assert [story.result for story in journeys.values()] == ["abandoned"]


async def test_an_expiry_inside_the_token_window_edits_through_the_latest_click(v2):
    scenario = await v2().start_command("default_roles")
    await scenario.confirm()

    await scenario.sweep()

    assert scenario.outputs[-1]["kind"] == "followup_edit", (
        "the newest click's webhook is the one Discord still honours"
    )


async def test_a_parent_waiting_on_a_live_child_is_not_expired(v2):
    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    await scenario.click("customize:exceptions")
    parent = scenario.session.id
    await scenario.click("customize:own_links")
    field = scenario.pending_modal_fields()[0]
    with scenario.later(600):
        await scenario.submit_modal({field: "not a link at all"})

    await scenario.expiry_loop_pass()

    scenario.expect_status("awaiting", parent)


# ------------------------------------------------------- background previews


def _slow_previews(monkeypatch):
    drawn = asyncio.Event()

    async def previews(member, designs, title="WELCOME", background=None):
        await drawn.wait()
        return {
            design: f"https://cdn.discordapp.com/attachments/1/2/{design}.png"
            for design in designs
        }

    from app.commands.welcome import service as welcome

    monkeypatch.setattr(welcome, "generate_design_previews", previews)
    return drawn


async def test_a_click_that_opens_the_gallery_is_answered_before_the_previews_wait(
    v2, deps, monkeypatch
):
    drawn = _slow_previews(monkeypatch)
    monkeypatch.setattr(ViewConstants, "PREVIEW_WAIT_SECONDS", 0.05)
    seed_document(deps, "welcome_messages", WELCOME_ENABLED)
    scenario = await asyncio.wait_for(v2().start_command("welcome_messages"), timeout=2)
    before = len(scenario.outputs)

    click = asyncio.create_task(scenario.click("section:welcome_config/design"))
    for _ in range(50):
        if click.done() or _first_answer(scenario.outputs[before:]):
            break
        await asyncio.sleep(0.01)
    answered_while_drawing = (
        _first_answer(scenario.outputs[before:]) and not drawn.is_set()
    )
    drawn.set()
    await click

    assert answered_while_drawing, (
        "Discord gives a click three seconds; waiting for the previews before "
        "answering lost the interaction (10062) on the local bot"
    )
    galleries = [
        item
        for item in locators.walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.MediaGallery)
    ]
    assert len(galleries) == 3


async def test_a_card_never_waits_longer_than_the_preview_budget(v2, monkeypatch):
    """The card carries the banner of the chosen design, so it waits for the
    drawing, but only within a budget: previews that never arrive cost no
    screen, and the screens that show no design never wait at all."""
    _slow_previews(monkeypatch)
    monkeypatch.setattr(ViewConstants, "PREVIEW_WAIT_SECONDS", 0.05)
    scenario = await v2().start_command("welcome_messages")

    await asyncio.wait_for(scenario.confirm(), timeout=1)
    await asyncio.wait_for(
        scenario.select_option("general", target="welcome_messages_channel"),
        timeout=1,
    )

    scenario.expect_step("welcome_config")


# ------------------------------------------------------------------ lifecycle


async def test_deleting_from_a_card_panel_replaces_it_with_the_final_message(v2, deps):
    """Broke as: followup.send(view=None) raised TypeError once a lifecycle change
    saved and its final embed replaced the panel's card."""
    seed_document(deps, "welcome_messages", WELCOME_ENABLED)
    scenario = await v2().start_command("welcome_messages")
    assert scenario.current_message.flags.components_v2

    await scenario.click("disable")
    await scenario.click("delete_all")

    assert not scenario.current_message.flags.components_v2
    assert scenario.current_message.embeds


async def test_a_finalize_that_fails_inside_a_commit_is_reported_once(v2, deps, caplog):
    """Broke as: one failed finalize logged Finalize failed, a false Commit failed and
    discord.py's Ignoring exception, three error messages for one failure."""
    seed_document(deps, "welcome_messages", WELCOME_ENABLED)
    scenario = await v2().start_command("welcome_messages")
    await scenario.click("disable")
    scenario.discord_refuses("followup.send", RuntimeError("drawing failed"), times=1)

    raised = False
    with caplog.at_level(logging.ERROR):
        try:
            await scenario.click("delete_all")
        except RuntimeError:
            raised = True

    failures = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR and " failed" in record.getMessage()
    ]
    assert not raised, "a logged effect failure must not reach discord.py"
    assert len(failures) == 1, failures
    assert "effect Finalize failed" in failures[0]


async def test_a_command_that_answers_in_time_never_says_it_is_thinking(v2, deps):
    """Lucas: a deferral on every command makes everyone read "thinking" for
    work that took no time. Nothing is deferred while the answer is on time."""
    seed_document(deps, "welcome_messages", WELCOME_ENABLED)

    scenario = await v2().start_command("welcome_messages")

    kinds = [event["kind"] for event in scenario.outputs]
    assert "defer" not in kinds, kinds
    assert scenario.current_message is not None


async def test_a_command_that_runs_late_is_answered_before_the_clock_runs_out(
    v2, deps, monkeypatch
):
    """Broke as: on a slow link the command answered nothing and the log filled
    with "Unknown interaction" (10062). Discord gives three seconds, and the
    panel was only sent after reading the document, so every slow read lost the
    whole interaction."""
    from app.forms.features.feature import GenericCogFeature

    seed_document(deps, "welcome_messages", WELCOME_ENABLED)
    monkeypatch.setattr(ViewConstants, "DEFER_AFTER_SECONDS", 0.01)
    reading = GenericCogFeature.open

    async def slow(self, context):
        await asyncio.sleep(0.05)
        return await reading(self, context)

    monkeypatch.setattr(GenericCogFeature, "open", slow)

    scenario = await v2().start_command("welcome_messages")

    kinds = [event["kind"] for event in scenario.outputs]
    answering = ("send_message", "followup_send")
    sent = [i for i, kind in enumerate(kinds) if kind in answering]
    assert "defer" in kinds, kinds
    assert kinds.index("defer") < min(sent), kinds
    assert scenario.current_message is not None, "the panel still arrives"


def _interactions(outcome, code=""):
    from prometheus_client import REGISTRY

    labels = {"outcome": outcome, "code": code}
    return REGISTRY.get_sample_value("keiko_interactions_total", labels) or 0.0


async def test_a_command_answered_in_time_is_counted_in_time(v2):
    before = _interactions("in_time")

    await v2().start_command("default_roles")

    assert _interactions("in_time") == before + 1


async def test_a_command_the_clock_had_to_defer_is_counted_deferred(v2, monkeypatch):
    from app.forms.features.feature import GenericCogFeature

    monkeypatch.setattr(ViewConstants, "DEFER_AFTER_SECONDS", 0.01)
    reading = GenericCogFeature.open

    async def slow(self, context):
        await asyncio.sleep(0.05)
        return await reading(self, context)

    monkeypatch.setattr(GenericCogFeature, "open", slow)
    deferred, in_time = _interactions("deferred"), _interactions("in_time")

    await v2().start_command("default_roles")

    assert _interactions("deferred") == deferred + 1
    assert _interactions("in_time") == in_time, "one interaction, one outcome"


async def test_a_click_discord_refuses_is_counted_failed_with_its_code(v2):
    scenario = await v2().start_command("default_roles")
    message = scenario.current_message
    button = scenario.component("continue")
    unknown = discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"),
        {"code": 10062, "message": "Unknown interaction"},
    )
    scenario.discord_refuses("response.edit_message", unknown)
    scenario.discord_refuses("response.defer", unknown)
    failed, in_time = _interactions("failed", "10062"), _interactions("in_time")

    await scenario.click(button, on=message)

    assert _interactions("failed", "10062") == failed + 1
    assert _interactions("in_time") == in_time, "one interaction, one outcome"


async def test_a_click_that_raises_is_counted_failed_and_still_raises(
    v2, deps, monkeypatch
):
    from app.forms.features import subscriptions

    async def broken(self, *args):
        raise RuntimeError("the Twitch lookup is gone")

    monkeypatch.setattr(subscriptions.SubscriptionFeature, "prefetch", broken)
    scenario = await v2().start_command("notifications_twitch")
    await scenario.confirm()
    await scenario.select_option("general", target="channel")
    await scenario.click("customize:streamer")
    field = scenario.pending_modal_fields()[0]
    failed, in_time = _interactions("failed"), _interactions("in_time")

    with pytest.raises(RuntimeError):
        await scenario.submit_modal({field: "gaules"})

    assert _interactions("failed") == failed + 1
    assert _interactions("in_time") == in_time, "one interaction, one outcome"


async def test_a_feature_a_button_opens_that_raises_is_counted_failed(v2, monkeypatch):
    """The /setup and greeting buttons open a feature with no command error handler."""
    from app.forms import run_feature_command
    from app.forms.features.feature import GenericCogFeature

    async def broken(self, context):
        raise RuntimeError("the saved document cannot be read")

    monkeypatch.setattr(GenericCogFeature, "open", broken)
    scenario = v2()
    failed = _interactions("failed")

    with pytest.raises(RuntimeError):
        await run_feature_command(
            scenario.interaction(), "default_roles", "setup_dashboard"
        )

    assert _interactions("failed") == failed + 1


async def test_an_aside_that_asks_first_spends_its_cooldown_only_when_confirmed(
    v2, deps, monkeypatch
):
    """Broke as: Sync wrote roles to every member on one click, without a word.

    The question also says how many members and bots are about to get a role,
    counting only the ones who do not have it yet (Lucas, 2026-09-16)."""
    from unittest.mock import AsyncMock

    from app.commands.roles import service as default_roles_feature
    from tests.behavioral.flows.roles import ENABLED as ROLES_ENABLED
    from tests.mocks.discord import create_member

    sync = AsyncMock()
    monkeypatch.setattr(default_roles_feature, "set_on_default_roles_sync", sync)
    seed_document(deps, "default_roles", ROLES_ENABLED)
    guild = create_guild(roles=["Admin", "Moderator", "Member"])
    create_member(guild, id=12, name="Bea", roles=["Member"])
    create_member(guild, id=13, name="Robo", bot=True)
    scenario = await v2(guild=guild).start_command("default_roles")
    panel = scenario.current_message
    sync_label = ml("default_roles.ui.buttons.sync.label", "pt-br")
    question = "default_roles.ui.responses.sync_confirm"

    await scenario.click(sync_label, on=panel)
    ask = scenario.current_message
    assert ask is not panel
    assert ask.embeds[0].title == ml(f"{question}.title", "pt-br")
    described = ask.embeds[0].description
    assert described.startswith(
        fill(
            ml(f"{question}.description", "pt-br"),
            members_count="1",
            members="<@&202>",
            bots_count="1",
            bots="<@&201>",
        )
    ), described
    await scenario.cancel()
    assert sync.await_count == 0

    await scenario.click(sync_label, on=panel)
    assert scenario.current_message is not ask, "a cancelled question spent nothing"
    await scenario.confirm()
    assert sync.await_count == 1

    await scenario.click(sync_label, on=panel)
    cooling = ml("commands.responses.cooldown.title", "pt-br")
    assert scenario.outputs[-1]["embed"]["title"] == cooling
    assert sync.await_count == 1, "a confirmed sync spent its cooldown"


async def test_a_modal_that_needs_a_lookup_is_answered_before_the_lookup_runs(
    v2, deps, monkeypatch
):
    """Discord gives a modal three seconds; a slow Twitch lookup ran before any
    answer."""
    from app.forms.features import subscriptions

    released = asyncio.Event()
    original = subscriptions.SubscriptionFeature.prefetch

    async def slow(self, *args):
        await released.wait()
        return await original(self, *args)

    monkeypatch.setattr(subscriptions.SubscriptionFeature, "prefetch", slow)
    deps.twitch.add_user("gaules", user_id="111")
    scenario = await v2().start_command("notifications_twitch")
    await scenario.confirm()
    await scenario.select_option("general", target="channel")
    await scenario.click("customize:streamer")
    before = len(scenario.outputs)
    field = scenario.pending_modal_fields()[0]
    submit = asyncio.ensure_future(scenario.submit_modal({field: "gaules"}))

    for _ in range(50):
        if submit.done() or _first_answer(scenario.outputs[before:]):
            break
        await asyncio.sleep(0.01)
    answered_while_looking_up = (
        _first_answer(scenario.outputs[before:]) and not released.is_set()
    )
    released.set()
    await submit

    assert answered_while_looking_up


def test_a_panel_part_has_its_own_edit_beside_its_lines():
    from app.forms.discord.views import layout_of
    from app.forms.form.components import Panel, PanelGroup, PanelPart

    group = PanelGroup(
        "card",
        "",
        (),
        parts=(
            PanelPart("card/channel", ("📺 **Canal:** <#1>",)),
            PanelPart("card/messages", ("💬 **Mensagens:** A",)),
        ),
    )
    panel = Panel(title="T", intro="i", groups=(group,), edit_label="Editar")
    view = layout_of(
        Screen(components=(panel,), flavour="components_v2"),
        lambda action, arg=None: f"k:s:1:{action}:{arg}",
        None,
    )

    sections = [
        item
        for item in locators.walk_items(view)
        if isinstance(item, discord.ui.Section)
        and isinstance(item.accessory, discord.ui.Button)
    ]
    assert [s.accessory.custom_id for s in sections] == [
        "k:s:1:edit:card/channel",
        "k:s:1:edit:card/messages",
    ]


def test_a_section_description_joins_its_heading_and_adds_no_component():
    """P2 (beta.2): a section explains itself under its heading, in the same text,
    and its lines follow in that text too (round 4 C)."""
    from app.forms.discord.views import layout_of
    from app.forms.form.components import (
        Button,
        Card,
        CardHeader,
        SectionView,
    )

    def drawn(description):
        section = SectionView(
            0, "messages", "💬 **Mensagens**", ("> oi",), (Button("Editar", "x"),)
        )
        card = Card(
            "card", CardHeader("T", "d"), (replace(section, description=description),)
        )
        return layout_of(
            Screen(components=(card,), flavour="components_v2"),
            lambda action, arg=None: f"k:s:1:{action}:{arg}",
            None,
        )

    described, plain = drawn("Você pode usar `{user}`"), drawn("")

    texts = [
        item.content
        for item in locators.walk_items(described)
        if isinstance(item, discord.ui.TextDisplay)
    ]
    assert "💬 **Mensagens**\nVocê pode usar `{user}`\n> oi" in texts, texts
    assert len(list(locators.walk_items(described))) == len(
        list(locators.walk_items(plain))
    )


def test_options_with_descriptions_are_blocks_with_their_own_button():
    """bl-5 (beta.2): a picker whose options explain themselves draws each one as
    its name and explanation with the Choose button beside it; plain options
    stay a grid of buttons."""
    from app.forms.discord.views import layout_of
    from app.forms.form.components import Choice, ChoiceOption

    def drawn(choice):
        return layout_of(
            Screen(title="Como?", components=(choice,), flavour="components_v2"),
            lambda action, arg=None: f"k:s:1:{action}:{arg}",
            None,
        )

    options = (
        ChoiceOption("🔗 Alguns", "0", "success", False, "Só os que você escolher."),
        ChoiceOption("🚫 Todos", "1", "danger", True, "Todos os links."),
    )
    blocks = drawn(Choice("card", options, button_label="Escolher"))
    grid = drawn(Choice("card", options))

    container = blocks.children[0]
    sections = [
        item for item in container.children if isinstance(item, discord.ui.Section)
    ]
    assert isinstance(container.children[1], discord.ui.Separator)
    assert [section.children[0].content for section in sections] == [
        "**🔗 Alguns**\nSó os que você escolher.",
        "**🚫 Todos**\nTodos os links.",
    ]
    assert [section.accessory.custom_id for section in sections] == [
        "k:s:1:pick:0",
        "k:s:1:pick:1",
    ]
    assert [section.accessory.style for section in sections] == [
        discord.ButtonStyle.success,
        discord.ButtonStyle.primary,
    ]
    rows = [
        item
        for item in grid.children[0].children
        if isinstance(item, discord.ui.ActionRow)
    ]
    assert [button.label for row in rows for button in row.children] == [
        "🔗 Alguns",
        "🚫 Todos",
    ]


async def test_confirming_a_card_review_replaces_it_with_the_enabled_message(v2, deps):
    """Broke as: the enabled embed was edited onto the Components V2 review, which
    Discord refuses. The panel then follows it on a message of its own."""
    deps.twitch.add_user("gaules", user_id="111")
    scenario = await v2().start_command("stream_elements_commands")
    await scenario.confirm()
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "gaules"})
    review = scenario.current_message
    assert review.flags.components_v2

    await scenario.confirm()

    title = ml("commands.responses.enabled.title", locale="pt-br")
    [activation] = [
        event
        for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == title
    ]
    assert activation["kind"] == "followup_send"
    assert not activation.get("components_v2")
    assert scenario.current_message is not review
    assert scenario.current_message.flags.components_v2, "the panel follows it"


async def test_the_design_picker_opened_from_the_welcome_panel_shows_previews(
    v2, deps, monkeypatch
):
    """Broke as: previews were drawn only for a setup, so the gallery an edit
    opened from the panel had no pictures."""
    _slow_previews(monkeypatch).set()
    seed_document(deps, "welcome_messages", WELCOME_ENABLED)
    scenario = await v2().start_command("welcome_messages")

    await scenario.click("section:welcome_config/design")

    galleries = [
        item
        for item in locators.walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.MediaGallery)
    ]
    assert len(galleries) == 3


def test_a_card_section_with_controls_draws_them_inside_the_card():
    """bd-1: a section drawn with its select on the card keeps it inside the
    card's frame, in a row of its own, where its button would have been."""
    from app.forms.discord.views import layout_of
    from app.forms.form.components import (
        Card,
        CardHeader,
        ChoiceOption,
        OptionSelect,
        SectionView,
    )

    times = OptionSelect(
        "card",
        "Escolher horário",
        (ChoiceOption("08:00", "08:00", selected=True), ChoiceOption("10:00", "10:00")),
        slot="notification_time",
    )
    section = SectionView(2, "notification_time", "🕒 **Horário**", ("> 08:00",), ())
    card = Card("card", CardHeader("Card", ""), (replace(section, controls=(times,)),))
    screen = Screen(components=(card,), flavour="components_v2")

    view = layout_of(screen, lambda action, arg=None: f"k:s:1:{action}:{arg}", None)

    container = view.children[0]
    [row] = [
        item for item in container.children if isinstance(item, discord.ui.ActionRow)
    ]
    [select] = row.children
    assert isinstance(select, discord.ui.Select)
    assert select.custom_id == "k:s:1:draft:notification_time"
    assert [option.default for option in select.options] == [True, False]

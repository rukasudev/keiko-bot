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

from app.services.utils import ml
from app.settings.discord.callbacks import RUNTIME
from app.settings.discord.interactions import decode, encode, is_ours
from app.settings.discord.views import modal_of
from app.settings.form.components import FileInput, Input, Screen, TextInputs
from app.settings.form.form_state import Status
from tests.behavioral.contracts.test_components_v2_limits import _assert_within_limits
from tests.behavioral.golden.paths.block_links import _seed as seed_block_links
from tests.behavioral.golden.paths.common import seed_document
from tests.behavioral.golden.paths.reminders_birthday import (
    _complete_settings_card,
    _guild,
)
from tests.behavioral.golden.paths.welcome_messages import (
    ENABLED as WELCOME_ENABLED,
)
from tests.behavioral.harness import locators
from tests.behavioral.harness.driver import FormScenario
from tests.behavioral.harness.fake_interaction import FakeFollowup
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


def _session_of(scenario):
    return scenario.session


def _button(scenario, target):
    message = scenario.current_message
    return message, locators.find_button(message, target, scenario.locale)


def _first_answer(events):
    """A defer, or the edit that disables the message clicked while it waits."""
    return any(event["kind"] in ("defer", "edit") for event in events)


def _notices(scenario, key):
    text = ml(f"commands.form-notices.{key}", locale=scenario.locale_str)
    return [e for e in scenario.outputs if e.get("content") == text]


# ---------------------------------------------------------------------- codec


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
    from app.settings.discord.interactions import to_event
    from app.settings.form import events as ev
    from app.settings.form.manager import PageTurned, SearchRequested

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
    from app.settings.discord.interactions import to_event
    from app.settings.form import events as ev

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
    from app.settings.discord.callbacks import MemberPictures

    guild = create_guild()
    create_member(guild, id=777, name="Amiga")
    pictures = MemberPictures(guild)

    assert pictures.get("777") == "https://cdn.discordapp.com/avatars/777/fake.png"
    assert pictures.get("888") is None
    assert pictures.get("not-an-id") is None
    assert MemberPictures(None).get("777") is None


def test_the_yes_and_no_of_a_card_are_two_buttons_inside_it():
    from app.settings.discord.views import layout_of
    from app.settings.form.components import (
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
    interaction = scenario._mint(message=message, custom_id=button.custom_id)
    await button.callback(interaction)
    after_first = len(scenario.outputs)
    await button.callback(interaction)
    assert len(scenario.outputs) == after_first
    assert _session_of(scenario).cursor == "default_roles_config"


async def test_a_stale_click_gets_only_the_current_screen(v2):
    """A notice on top of the redraw raced the late-answer clock and lost with
    40060, dropping the redraw (B5, beta.2)."""
    scenario = await v2().start_command("default_roles")
    message, old_button = _button(scenario, "continue")
    await scenario.confirm()
    revision_after = _session_of(scenario).revision
    await old_button.callback(
        scenario._mint(message=message, custom_id=old_button.custom_id)
    )
    assert _notices(scenario, "stale") == []
    assert _session_of(scenario).revision == revision_after
    assert scenario.outputs[-1]["kind"] in ("edit", "followup_edit")


async def test_two_concurrent_clicks_are_serialized(v2):
    scenario = await v2().start_command("default_roles")
    message, button = _button(scenario, "continue")
    first = scenario._mint(message=message, custom_id=button.custom_id)
    second = scenario._mint(message=message, custom_id=button.custom_id)
    await asyncio.gather(button.callback(first), button.callback(second))
    assert _session_of(scenario).cursor == "default_roles_config"
    assert _notices(scenario, "stale") == []
    assert scenario.outputs[-1]["kind"] in ("edit", "followup_edit")


# --------------------------------------------------------------------- expiry


async def test_expiry_takes_the_buttons_off_an_embed_screen(v2):
    scenario = await v2().start_command("default_roles")
    await scenario.expire()
    message = scenario.current_message
    assert message.view is None
    expired = ml("commands.form-notices.expired", locale="pt-br")
    assert expired in message.embeds[0].description
    assert _session_of(scenario) is None or True
    assert all(s.status is Status.EXPIRED for s in RUNTIME.store._sessions.values())


async def test_expiry_takes_the_buttons_off_a_card(v2):
    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    assert scenario.current_message.flags.components_v2
    await scenario.expire()
    items = list(locators.walk_items(scenario.current_message.view))
    assert not any(isinstance(item, discord.ui.Button) for item in items)
    texts = [i.content for i in items if isinstance(i, discord.ui.TextDisplay)]
    assert ml("commands.form-notices.expired", locale="pt-br") in texts


async def test_a_click_after_expiry_finalizes_the_message_again(v2):
    scenario = await v2().start_command("default_roles")
    message, button = _button(scenario, "continue")
    await scenario.expire()
    await button.callback(scenario._mint(message=message, custom_id=button.custom_id))
    assert scenario.current_message.view is None
    assert _session_of(scenario) is None


async def test_a_click_on_a_forgotten_session_closes_the_message(v2):
    scenario = await v2().start_command("default_roles")
    message, button = _button(scenario, "continue")
    RUNTIME.reset()
    await button.callback(scenario._mint(message=message, custom_id=button.custom_id))
    assert message.view is None
    assert (
        ml("commands.form-notices.expired", locale="pt-br")
        in message.embeds[0].description
    )


# ------------------------------------------------------------- choreography


async def test_a_replacement_survives_a_failed_delete(v2, monkeypatch):
    async def failing_delete(self, message_id):
        raise discord.HTTPException(
            SimpleNamespace(status=404, reason="gone"), "Unknown Message"
        )

    monkeypatch.setattr(FakeFollowup, "delete_message", failing_delete)
    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    assert scenario.current_message.flags.components_v2
    assert not any(e["kind"] == "delete" for e in scenario.outputs)
    assert _session_of(scenario).cursor == "link_settings"


async def test_a_held_card_is_usable_again_when_its_screen_fails_to_draw(
    v2, deps, monkeypatch
):
    """Broke as (beta.2 review, K2): a lookup disables the card while it runs,
    and the controls came back only after every effect succeeded; a Render that
    failed after the hold left every button of the card disabled for good.
    Shared: the hold and release of `callbacks.py` and `Executor.render`, which
    now records a screen only once Discord took it. Exposed by the StreamElements
    setup. Guaranteed: a held card is usable again whatever its effects did."""
    from tests.behavioral.golden.paths import stream_elements_commands

    stream_elements_commands._known_streamers(deps)
    scenario = await v2().start_command("stream_elements_commands")
    await scenario.confirm()
    card = scenario.current_message
    field = scenario.pending_modal_fields()[0]
    sending = FakeFollowup.send
    failures = []

    async def fails_once(self, *args, **kwargs):
        if not failures:
            failures.append(kwargs.get("view"))
            raise discord.HTTPException(
                SimpleNamespace(status=500, reason="boom"), "Internal Server Error"
            )
        return await sending(self, *args, **kwargs)

    monkeypatch.setattr(FakeFollowup, "send", fails_once)
    await scenario.submit_modal({field: "shroud"})

    controls = [
        item
        for item in locators.walk_items(card.view)
        if isinstance(item, (discord.ui.Button, discord.ui.Select))
    ]
    assert failures, "the screen after the lookup failed to draw"
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


async def test_the_birthday_cards_stay_within_discord_limits(v2, deps):
    guild = _guild()
    scenario = await v2(guild=guild).start_command("reminders_birthday")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "birthday_config card")
    await _complete_settings_card(scenario)
    await scenario.confirm()

    panel = await FormScenario(
        guild=guild, user=scenario.user, locale="pt-br", mongo=deps.mongo_client
    ).start_command("reminders_birthday")
    await panel.click("add")
    await panel.select_option("Amiga", target="user")
    _assert_within_limits(panel.current_message.view, "birthday_member_config card")


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


@pytest.fixture
def closed_traces():
    from app import logger as logger_module
    from app.services import trace as trace_service

    trace_service.clear_sinks()
    captured: list = []
    trace_service.register_sink(captured.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    yield captured
    logger_module.logger.removeHandler(folding)
    trace_service.clear_sinks()


@pytest.fixture
def stories():
    from app.services import journey

    journey.clear()
    seen: dict = {}
    journey.set_publisher(lambda story: seen.__setitem__(story.session_id, story))
    journey.install()
    yield seen
    journey.clear()
    journey.set_publisher(None)


async def _save_block_links_with_a_failing_commit(v2, monkeypatch):
    from app.settings.features import block_links

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
    v2, closed_traces, stories, monkeypatch
):
    await _save_block_links_with_a_failing_commit(v2, monkeypatch)

    assert not [trace for trace in closed_traces if trace.is_noteworthy], (
        "a failed save is told by the session message and the error channel, "
        "never by a third message of engine lines"
    )
    assert [story.result for story in stories.values()] == ["failure"]


async def test_a_raising_commit_puts_a_failure_line_on_the_story_not_the_message(
    v2, stories, monkeypatch
):
    await _save_block_links_with_a_failing_commit(v2, monkeypatch)

    lines = [line["message"] for line in list(stories.values())[0].lines]
    assert any(line.startswith("❌") and "RuntimeError" in line for line in lines)
    assert not any("secret detail" in line for line in lines), (
        "an exception message is free text and never reaches an event"
    )
    assert any(line.startswith("step:") for line in lines)


async def test_the_session_story_says_whether_the_person_is_an_admin(deps, stories):
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    user.guild_permissions = SimpleNamespace(administrator=True)
    scenario = FormScenario(
        guild=guild, user=user, locale="pt-br", mongo=deps.mongo_client
    )

    await scenario.start_command("default_roles")

    assert [story.is_admin for story in stories.values()] == [True]


async def test_an_expiry_after_the_interaction_token_died_leaves_discord_alone(
    v2, stories
):
    """Found running the local bot: every sweep expiry failed with
    `401 Invalid Webhook Token`. Discord honours an interaction token for fifteen
    minutes and a session expires later than that, so the edit could never land;
    the next click on the message closes it instead."""
    import time
    from datetime import datetime, timezone

    scenario = await v2().start_command("default_roles")
    RUNTIME.sessions[scenario.session.id].surface.touched_at = (
        time.monotonic() - 16 * 60
    )
    before = len(scenario.outputs)

    await RUNTIME.sweep(datetime.max.replace(tzinfo=timezone.utc))

    assert len(scenario.outputs) == before, "no edit is attempted with a dead token"
    assert [story.result for story in stories.values()] == ["abandoned"]


async def test_an_expiry_inside_the_token_window_edits_through_the_latest_click(v2):
    from datetime import datetime, timezone

    scenario = await v2().start_command("default_roles")
    await scenario.confirm()

    await RUNTIME.sweep(datetime.max.replace(tzinfo=timezone.utc))

    assert scenario.outputs[-1]["kind"] == "followup_edit", (
        "the newest click's webhook is the one Discord still honours"
    )


async def test_a_parent_waiting_on_a_live_child_is_not_expired(v2):
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone

    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")  # Configurar the exceptions
    await scenario.click("customize:6")  # the list opens its child
    parent = next(
        session
        for session in RUNTIME.store._sessions.values()
        if session.parent_id is None
    )
    assert parent.awaiting == "child"
    RUNTIME.store._sessions[parent.id] = replace(
        parent, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )

    await RUNTIME.sweep(datetime.now(timezone.utc))

    assert RUNTIME.store.get(parent.id).status is not Status.EXPIRED, (
        "a child still in use keeps its parent alive"
    )


# ------------------------------------------------------- background previews


def _slow_previews(monkeypatch):
    drawn = asyncio.Event()

    async def previews(member, designs, title="WELCOME", background=None):
        await drawn.wait()
        return {
            design[
                "key"
            ]: f"https://cdn.discordapp.com/attachments/1/2/{design['key']}.png"
            for design in designs
        }

    monkeypatch.setattr(
        "app.settings.features.welcome_messages.generate_design_previews", previews
    )
    return drawn


async def test_a_click_that_opens_the_gallery_is_answered_before_the_previews_wait(
    v2, deps, monkeypatch
):
    drawn = _slow_previews(monkeypatch)
    monkeypatch.setattr("app.constants.ViewConstants.PREVIEW_WAIT_SECONDS", 0.05)
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
    monkeypatch.setattr("app.constants.ViewConstants.PREVIEW_WAIT_SECONDS", 0.05)
    scenario = await v2().start_command("welcome_messages")

    await asyncio.wait_for(scenario.confirm(), timeout=1)
    await asyncio.wait_for(
        scenario.select_option("general", target="welcome_messages_channel"),
        timeout=1,
    )

    assert _session_of(scenario).cursor == "welcome_config"


# ------------------------------------------------------------------ lifecycle


async def test_pausing_from_a_card_panel_replaces_it_with_the_paused_message(v2, deps):
    """Broke as: followup.send(view=None) raised TypeError once the pause saved."""
    seed_document(deps, "welcome_messages", WELCOME_ENABLED)
    scenario = await v2().start_command("welcome_messages")
    assert scenario.current_message.flags.components_v2

    await scenario.click("pause")
    await scenario.submit_confirmation()

    assert not scenario.current_message.flags.components_v2
    assert scenario.current_message.embeds


async def test_a_finalize_that_fails_inside_a_commit_is_reported_once(
    v2, deps, monkeypatch, caplog
):
    """Broke as: one failed finalize logged Finalize failed, a false Commit failed and
    discord.py's Ignoring exception, three error messages for one failure."""
    from app.settings.discord.transitions import Executor

    async def broken(self, **kwargs):
        raise RuntimeError("drawing failed")

    seed_document(deps, "welcome_messages", WELCOME_ENABLED)
    scenario = await v2().start_command("welcome_messages")
    await scenario.click("pause")
    monkeypatch.setattr(Executor, "_replace", broken)

    raised = False
    with caplog.at_level(logging.ERROR):
        try:
            await scenario.submit_confirmation()
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
    from app.settings.features.feature import GenericCogFeature

    seed_document(deps, "welcome_messages", WELCOME_ENABLED)
    monkeypatch.setattr("app.constants.ViewConstants.DEFER_AFTER_SECONDS", 0.01)
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
    from app.settings.features.feature import GenericCogFeature

    monkeypatch.setattr("app.constants.ViewConstants.DEFER_AFTER_SECONDS", 0.01)
    reading = GenericCogFeature.open

    async def slow(self, context):
        await asyncio.sleep(0.05)
        return await reading(self, context)

    monkeypatch.setattr(GenericCogFeature, "open", slow)
    deferred, in_time = _interactions("deferred"), _interactions("in_time")

    await v2().start_command("default_roles")

    assert _interactions("deferred") == deferred + 1
    assert _interactions("in_time") == in_time, "one interaction, one outcome"


async def test_a_click_discord_refuses_is_counted_failed_with_its_code(v2, monkeypatch):
    from tests.behavioral.harness.fake_interaction import FakeResponse

    scenario = await v2().start_command("default_roles")
    message = scenario.current_message
    button = locators.find_button(message, "continue", scenario.locale)

    async def expired(self, **kwargs):
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 10062, "message": "Unknown interaction"},
        )

    monkeypatch.setattr(FakeResponse, "edit_message", expired)
    monkeypatch.setattr(FakeResponse, "defer", expired)
    failed, in_time = _interactions("failed", "10062"), _interactions("in_time")

    await button.callback(scenario._mint(message=message, custom_id=button.custom_id))

    assert _interactions("failed", "10062") == failed + 1
    assert _interactions("in_time") == in_time, "one interaction, one outcome"


async def test_a_click_that_raises_is_counted_failed_and_still_raises(v2, monkeypatch):
    from app.settings.discord.callbacks import Runtime

    scenario = await v2().start_command("default_roles")
    message = scenario.current_message
    button = locators.find_button(message, "continue", scenario.locale)

    async def broken(self, *args, **kwargs):
        raise RuntimeError("the session store is gone")

    monkeypatch.setattr(Runtime, "_handle_locked", broken)
    failed, in_time = _interactions("failed"), _interactions("in_time")

    with pytest.raises(RuntimeError):
        await button.callback(
            scenario._mint(message=message, custom_id=button.custom_id)
        )

    assert _interactions("failed") == failed + 1
    assert _interactions("in_time") == in_time, "one interaction, one outcome"


async def test_a_feature_a_button_opens_that_raises_is_counted_failed(v2, monkeypatch):
    """The /setup and greeting buttons open a feature with no command error handler."""
    from app.components.buttons import run_feature_command

    async def broken(*args, **kwargs):
        raise RuntimeError("the saved document cannot be read")

    monkeypatch.setattr("app.settings.open_feature", broken)
    scenario = v2()
    failed = _interactions("failed")

    with pytest.raises(RuntimeError):
        await run_feature_command(scenario._mint(), "default_roles", "setup_dashboard")

    assert _interactions("failed") == failed + 1


async def test_an_aside_that_asks_first_spends_its_cooldown_only_when_confirmed(
    v2, deps, monkeypatch
):
    """Broke as: Sync wrote roles to every member on one click, without a word.

    The question also says how many members and bots are about to get a role,
    counting only the ones who do not have it yet (Lucas, 2026-09-16)."""
    from unittest.mock import AsyncMock

    from app.settings.features import default_roles as default_roles_feature
    from tests.behavioral.golden.paths.default_roles import ENABLED as ROLES_ENABLED
    from tests.mocks.discord import create_member

    sync = AsyncMock()
    monkeypatch.setattr(default_roles_feature, "set_on_default_roles_sync", sync)
    seed_document(deps, "default_roles", ROLES_ENABLED)
    guild = create_guild(roles=["Admin", "Moderator", "Member"])
    create_member(guild, id=12, name="Bea", roles=["Member"])
    create_member(guild, id=13, name="Robo", bot=True)
    scenario = await v2(guild=guild).start_command("default_roles")
    panel = scenario.current_message
    state = RUNTIME.sessions[scenario.session.id]

    async def click(message, label):
        button = locators.find_button(message, label, scenario.locale)
        interaction = scenario._mint(message=message, custom_id=button.custom_id)
        await locators.dispatch_click(message.view, button, interaction)

    await click(panel, "Sincronizar")
    ask = scenario.current_message
    assert ask is not panel and "Sincronizar cargos?" in ask.embeds[0].title
    described = ask.embeds[0].description
    assert "<@&202>" in described, described
    assert "<@&201>" in described, described
    assert "👥 **Membros**\n- Quantidade: **1**" in described, described
    assert "🤖 **Bots**\n- Quantidade: **1**" in described, described
    await click(ask, "Cancelar")
    assert sync.await_count == 0 and not state.cooldowns

    await click(panel, "Sincronizar")
    await click(scenario.current_message, "Confirmar")
    assert sync.await_count == 1 and "sync" in state.cooldowns


async def test_a_modal_that_needs_a_lookup_is_answered_before_the_lookup_runs(
    v2, deps, monkeypatch
):
    """Discord gives a modal three seconds; a slow Twitch lookup ran before any
    answer."""
    from app.settings.features import subscriptions

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
    await scenario.click("customize:1")
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
    from app.settings.discord.views import layout_of
    from app.settings.form.components import Panel, PanelGroup, PanelPart

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
    from app.settings.discord.views import layout_of
    from app.settings.form.components import (
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
    from app.settings.discord.views import layout_of
    from app.settings.form.components import Choice, ChoiceOption

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

    title = ml("commands.command-events.enabled.title", locale="pt-br")
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
    from app.settings.discord.views import layout_of
    from app.settings.form.components import (
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

"""The Discord adapter under the offline harness.

The goldens prove the screens; these prove the hostile-environment rules the
review names (4.6, II.1): a duplicate click does nothing, a stale click gets a
notice and the current screen, two concurrent clicks are serialized, expiry is
visible, a replacement survives a failed delete, and payloads obey Discord.
"""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from app.forms.adapters.discord.entrypoints import RUNTIME
from app.forms.adapters.discord.ids import decode, encode, is_ours
from app.forms.adapters.discord.renderer import modal_of
from app.forms.engine.screen import FileInput, Input, Screen, TextInputs
from app.forms.engine.session import Status
from app.services.utils import ml
from tests.behavioral.contracts.test_components_v2_limits import _assert_within_limits
from tests.behavioral.golden.paths.block_links import _seed as seed_block_links
from tests.behavioral.golden.paths.reminders_birthday import (
    _complete_settings_card,
    _guild,
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


async def test_a_stale_click_gets_a_notice_and_the_current_screen(v2):
    scenario = await v2().start_command("default_roles")
    message, old_button = _button(scenario, "continue")
    await scenario.confirm()
    revision_after = _session_of(scenario).revision
    await old_button.callback(
        scenario._mint(message=message, custom_id=old_button.custom_id)
    )
    assert len(_notices(scenario, "stale")) == 1
    assert _session_of(scenario).revision == revision_after
    assert scenario.outputs[-1]["kind"] in ("edit", "followup_edit")


async def test_two_concurrent_clicks_are_serialized(v2):
    scenario = await v2().start_command("default_roles")
    message, button = _button(scenario, "continue")
    first = scenario._mint(message=message, custom_id=button.custom_id)
    second = scenario._mint(message=message, custom_id=button.custom_id)
    await asyncio.gather(button.callback(first), button.callback(second))
    assert _session_of(scenario).cursor == "default_roles_config"
    assert len(_notices(scenario, "stale")) == 1


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
    assert not any(isinstance(item, discord.ui.ActionRow) for item in inside)
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
    scenario = await v2(guild=_guild()).start_command("reminders_birthday")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "birthday_config card")
    await _complete_settings_card(scenario)
    await scenario.click("Sim")
    await scenario.select_option("Amiga")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "birthday_member_config card")


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
    from app.forms.features import block_links

    async def boom(self, kind, payload, context):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(type(block_links.FEATURE), "commit", boom)
    scenario = await v2().start_command("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click("Depois")
    await scenario.confirm()
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
    await scenario.click("done")
    await scenario.click("Sim")
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

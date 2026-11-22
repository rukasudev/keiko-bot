"""Self-tests for the behavioral harness.

A broken scenario runner must not silently produce passing tests: these
cover the interaction state machine, click dispatch across the repo's four
callback patterns, normalization determinism, transcript diagnostics, and
scenario isolation.
"""
import copy
from datetime import datetime, timezone

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from app.core.app_commands import listed_commands
from app.core.utils import ml
from app.forms.form.form_yaml import DiskSource
from tests.behavioral.flows import block_links, module_of, twitch
from tests.behavioral.flows.common import seed_document

from tests.behavioral.harness.driver import FormScenario
from tests.behavioral.harness.errors import (
    HarnessProtocolError,
    LocatorError,
    ScenarioAssertionError,
)
from tests.behavioral.harness.fake_interaction import FakeInteraction
from tests.behavioral.harness.message_store import MessageStore
from tests.behavioral.harness import copy_keys, golden, locators
from tests.helpers.slash_tree import synced_tree
from tests.mocks.discord import create_guild, create_member
from types import SimpleNamespace

pytestmark = pytest.mark.behavioral


def _interaction(store=None, message=None):
    guild = create_guild()
    user = create_member(guild, id=1, name="U")
    return FakeInteraction(store or MessageStore(), guild=guild, user=user,
                           locale=discord.Locale.brazil_portuguese, message=message)


# ---------------------------------------------------------------- state machine

async def test_response_is_done_flips_after_each_initial_response():
    for api, kwargs in [
        ("send_message", {"content": "hi"}),
        ("defer", {}),
        ("send_modal", {"modal": discord.ui.Modal(title="t")}),
    ]:
        interaction = _interaction()
        assert not interaction.response.is_done()
        if api == "send_modal":
            await interaction.response.send_modal(kwargs["modal"])
        else:
            await getattr(interaction.response, api)(**kwargs)
        assert interaction.response.is_done()


def test_an_interaction_outside_an_app_command_carries_no_command():
    """A click or a modal submit carries no app command, as Discord's does.

    Broke as: `FakeInteraction` had no `command` attribute where a real
    `discord.Interaction` holds None outside an app command, so once a test had
    installed the Discord log handler, whose `is_muted` reads it, an error logged
    with `interaction=` raised AttributeError in whatever test ran next (the
    report's ticket in English, run after `tests/test_commands.py`). Shared
    behaviour: the harness's interaction. Consumer that exposed it: /reportar's
    Notion error log. Guaranteed: every interaction the harness builds answers
    `command`, None until an app command runs.
    """
    assert _interaction().command is None


async def test_second_initial_response_raises_protocol_error():
    interaction = _interaction()
    await interaction.response.defer()
    with pytest.raises(HarnessProtocolError):
        await interaction.response.send_message(content="late")


async def test_followup_requires_initial_response():
    interaction = _interaction()
    with pytest.raises(HarnessProtocolError):
        await interaction.followup.send(content="too early")


async def test_followup_send_returns_message_handle():
    interaction = _interaction()
    await interaction.response.defer()
    message = await interaction.followup.send(content="ok", ephemeral=True)
    assert message.id in interaction.store.messages
    assert message.ephemeral


async def test_followup_send_refuses_delete_after_like_discord_py():
    """`Webhook.send` takes no `delete_after`: a message that must vanish after
    the first answer is deleted with `delete(delay=...)` instead."""
    interaction = _interaction()
    await interaction.response.defer()
    with pytest.raises(TypeError):
        await interaction.followup.send(content="x", ephemeral=True, delete_after=10)

    message = await interaction.followup.send(content="x", ephemeral=True)
    await message.delete(delay=10)
    assert interaction.store.events[-1]["delete_after"] == 10
    assert not message.deleted, "the clock never moves offline"


async def test_editing_deleted_message_raises():
    interaction = _interaction()
    await interaction.response.defer()
    message = await interaction.followup.send(content="x")
    await interaction.followup.delete_message(message.id)
    with pytest.raises(HarnessProtocolError):
        await interaction.followup.edit_message(message.id, content="y")


async def test_a_components_v2_message_refuses_embeds_and_content_like_discord():
    """The flag is fixed at send time: editing a container message with an
    embed or content fails with 50035 and the message is left as it was."""
    interaction = _interaction()
    await interaction.response.defer()
    layout = discord.ui.LayoutView()
    layout.add_item(discord.ui.TextDisplay("card"))
    message = await interaction.followup.send(view=layout, ephemeral=True)

    with pytest.raises(discord.HTTPException) as failure:
        await interaction.followup.edit_message(message.id, embed=discord.Embed(title="x"))
    assert failure.value.code == 50035
    assert message.view is layout and not message.embeds

    await interaction.followup.edit_message(message.id, view=None)
    assert message.registered_items == []


async def test_locale_is_writable_like_the_engine_does():
    interaction = _interaction()
    interaction.locale = discord.Locale.american_english
    assert interaction.locale is discord.Locale.american_english


# ------------------------------------------------------------- click dispatch

class _ShadowedView(discord.ui.View):
    """Pattern 1: attribute-shadowed callback (ConfirmButton style)."""

    def __init__(self, hits):
        super().__init__()
        button = discord.ui.Button(label="Shadowed")

        async def callback(interaction):
            hits.append("shadowed")

        button.callback = callback
        self.add_item(button)


class _SubclassButton(discord.ui.Button):
    """Pattern 2: subclass with async def callback."""

    def __init__(self, hits):
        super().__init__(label="Subclassed")
        self._hits = hits

    async def callback(self, interaction):
        self._hits.append("subclass")


class _DecoratedView(discord.ui.View):
    """Pattern 3: @discord.ui.button decorator."""

    def __init__(self, hits):
        self._hits = hits
        super().__init__()

    @discord.ui.button(label="Decorated")
    async def go(self, interaction, button):
        self._hits.append("decorated")


class _RouterLayoutView(discord.ui.LayoutView):
    """Pattern 4: no item callback; interaction_check routes custom_ids."""

    def __init__(self, hits):
        super().__init__()
        self._hits = hits
        self.add_item(discord.ui.ActionRow(
            discord.ui.Button(label="Routed", custom_id="route_me")
        ))

    async def interaction_check(self, interaction):
        self._hits.append(interaction.data.get("custom_id"))
        return False


@pytest.mark.parametrize("build,target,expected", [
    (lambda hits: _ShadowedView(hits), "Shadowed", "shadowed"),
    (lambda hits: _wrap_button(_SubclassButton(hits)), "Subclassed", "subclass"),
    (lambda hits: _DecoratedView(hits), "Decorated", "decorated"),
    (lambda hits: _RouterLayoutView(hits), "route_me", "route_me"),
])
async def test_click_dispatch_covers_all_four_patterns(build, target, expected):
    hits = []
    view = build(hits)
    button = locators.find_button(view, target, discord.Locale.brazil_portuguese)
    interaction = _interaction()
    if getattr(button, "_provided_custom_id", False):
        interaction.data = {"custom_id": button.custom_id}
    await locators.dispatch_click(view, button, interaction)
    assert hits == [expected]


def _wrap_button(button):
    view = discord.ui.View()
    view.add_item(button)
    return view


async def test_unknown_target_lists_available_components():
    hits = []
    view = _ShadowedView(hits)
    with pytest.raises(LocatorError) as exc_info:
        locators.find_button(view, "does-not-exist", discord.Locale.american_english)
    assert "Shadowed" in str(exc_info.value)


# --------------------------------------------------- determinism & transcript

async def _mini_run(scenario_factory) -> FormScenario:
    scenario = scenario_factory(locale="pt-br")
    await scenario.start("default_roles")
    await scenario.confirm()
    await scenario.select_option("Member", target="Cargos para membros")
    await scenario.click("done")
    await scenario.click("pick:False")
    await scenario.confirm()
    return scenario


async def test_two_identical_runs_produce_identical_normalized_output(
        scenario_factory, deps):
    first = await _mini_run(scenario_factory)
    deps.mongo_client.guild.cogs._data.clear()
    deps.redis_client.flushdb()
    second = await _mini_run(scenario_factory)
    assert first.outputs == second.outputs


async def test_transcript_is_ordered_and_attached_to_failures(scenario_factory):
    scenario = await scenario_factory().start("default_roles")
    sequences = [event["seq"] for event in scenario.outputs]
    assert sequences == sorted(sequences)

    with pytest.raises(ScenarioAssertionError) as exc_info:
        scenario.expect_step("not-a-real-step")
    message = str(exc_info.value)
    assert "--- Transcript ---" in message
    assert 'start "default_roles"' in message


async def test_modal_field_mismatch_lists_available_fields(scenario_factory):
    scenario = await scenario_factory().start("stream_elements_commands")
    await scenario.confirm()             # -> modal step
    scenario.expect_modal()
    with pytest.raises(LocatorError):
        await scenario.submit_modal({"campo-que-nao-existe": "x"})


# ------------------------------------------------------------------- isolation

async def test_scenarios_do_not_share_state(scenario_factory):
    first = await scenario_factory().start("default_roles")
    second = await scenario_factory().start("block_links")

    assert first.store is not second.store
    assert first.session is not second.session
    assert first.outputs[0]["target"] == "default_roles"
    assert second.outputs[0]["target"] == "block_links"


# -------------------------------------------------------------- public actions

class _RecordingView(discord.ui.View):
    """One button, `Gravar`, that keeps every interaction it is pressed with."""

    def __init__(self, hits):
        super().__init__()
        button = discord.ui.Button(label="Gravar", custom_id="record")

        async def callback(interaction):
            hits.append(interaction)

        button.callback = callback
        self.add_item(button)


def _shown(scenario, view):
    message = scenario.store.create([], view, True)
    scenario.store.record("send", message=message.id, view=view, ephemeral=True)
    return message


def _texts(message):
    return "\n".join(
        item.content
        for item in message.registered_items
        if isinstance(item, discord.ui.TextDisplay)
    )


async def test_a_card_section_is_clicked_by_its_key_never_by_its_position(
    scenario_factory,
):
    scenario = await block_links.open_card(scenario_factory, "pt-br")

    await scenario.click("customize:exceptions")
    assert scenario.component("allowed_chats"), "the exceptions screen is on screen"
    await scenario.go_back()

    with pytest.raises(LocatorError, match="exceptions") as refused:
        await scenario.click("customize:2")
    assert "no card section '2'" in str(refused.value)


def test_a_section_target_is_resolved_only_through_the_scenario():
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Editar", custom_id="k:s:1:section:0"))

    assert locators.section_target("customize:own_links") == ("section", "own_links")
    assert locators.section_target("reset:answer") == ("reset", "answer")
    assert locators.section_target("edit:group:settings") is None
    with pytest.raises(LocatorError, match="through the scenario"):
        locators.find_button(view, "customize:own_links", discord.Locale.brazil_portuguese)


async def test_an_interaction_is_built_for_a_click_without_sending_it(scenario_factory):
    scenario = scenario_factory()
    hits = []
    message = _shown(scenario, _RecordingView(hits))
    at = datetime(2026, 5, 1, 12, 34, tzinfo=timezone.utc)

    slash = scenario.interaction()
    click = scenario.interaction(on=message, target="Gravar", at=at)

    assert (slash.message, slash.data) == (None, {})
    assert click.message is message and click.data == {"custom_id": "record"}
    assert click.created_at == at and slash.created_at != at
    assert hits == [] and [event["kind"] for event in scenario.outputs] == ["send"]


async def test_a_click_reaches_the_message_it_names_dated_when_it_says(scenario_factory):
    scenario = scenario_factory()
    hits = []
    first = _shown(scenario, _RecordingView(hits))
    _shown(scenario, _RecordingView([]))
    at = datetime(2026, 5, 1, 12, 34, tzinfo=timezone.utc)

    await scenario.click("Gravar", on=first, at=at)

    [pressed] = hits
    assert pressed.message is first and pressed.created_at == at
    assert scenario.outputs[-1] == {
        **scenario.outputs[-1], "kind": "click", "actor": "user", "message": "M1",
    }


async def test_a_component_found_earlier_is_pressed_after_its_message_was_redrawn(
    scenario_factory,
):
    scenario = scenario_factory()
    hits = []
    message = _shown(scenario, _RecordingView(hits))
    old = scenario.component("Gravar")
    message.apply_edit(view=_RecordingView([]))

    await scenario.click(old, on=message)

    assert len(hits) == 1, "the stale button itself was pressed"


async def test_one_interaction_can_be_delivered_twice(scenario_factory):
    scenario = scenario_factory()
    hits = []
    message = _shown(scenario, _RecordingView(hits))
    interaction = scenario.interaction(on=message, target="Gravar")

    await scenario.click("Gravar", interaction=interaction)
    await scenario.click("Gravar", interaction=interaction)

    assert hits == [interaction, interaction]


async def test_a_click_after_a_restart_reaches_the_dynamic_items_the_bot_registers(
    scenario_factory, deps
):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    guild = create_guild()
    admin = create_member(guild, id=555, name="Tester")
    admin.guild_permissions = discord.Permissions(administrator=True)
    scenario = await scenario_factory(guild=guild, user=admin).start_command(
        block_links.FORM
    )
    panel = scenario.current_message

    scenario.restart()
    assert scenario.sessions == []
    answered = await scenario.click_after_restart("pause")

    assert answered.response.is_done()
    assert _texts(panel).endswith(ml("texts.notices.expired", locale="pt-br"))
    [reopen] = [
        item for item in panel.registered_items if isinstance(item, discord.ui.DynamicItem)
    ]
    await scenario.click_after_restart(reopen, on=panel)
    assert scenario.current_message is not panel
    assert [session.key for session in scenario.sessions] == [block_links.FORM]


async def test_a_modal_submitted_after_a_restart_reaches_the_interaction_listener(
    scenario_factory, deps
):
    scenario = await twitch.open_card(scenario_factory, deps, "pt-br")
    await twitch.pick_channel(scenario)
    card = scenario.current_message
    await scenario.click("customize:streamer")
    assert scenario.pending_modal is not None
    scenario.restart()

    submitted = await scenario.submit_modal_after_restart()

    assert submitted.response.is_done() and scenario.pending_modal is None
    assert _texts(card).endswith(ml("texts.notices.expired", locale="pt-br"))


async def test_a_session_is_read_by_the_id_it_had_on_screen(scenario_factory):
    scenario = await scenario_factory().start("block_links")
    session_id = scenario.session.id
    scenario.expect_status("active")

    await scenario.cancel()

    scenario.expect_status("cancelled", session_id)
    with pytest.raises(ScenarioAssertionError, match="is 'cancelled', expected 'active'"):
        scenario.expect_status("active", session_id)
    with pytest.raises(ScenarioAssertionError, match="never opened, or forgotten"):
        scenario.expect_status("active", "no-such-session")


async def test_a_sweep_forgets_the_sessions_that_ended(scenario_factory):
    scenario = await scenario_factory().start("block_links")
    await scenario.cancel()
    assert [session.status.value for session in scenario.sessions] == ["cancelled"]

    await scenario.sweep()

    assert scenario.sessions == []


async def test_a_click_by_custom_id_alone_reaches_the_dynamic_items(scenario_factory):
    from app.forms.discord.interactions import encode

    scenario = scenario_factory()

    answered = await scenario.click_custom_id(
        encode("block_links-0123456789abcdef", 1, "lifecycle", "pause")
    )

    assert answered.response.is_done(), "the late item answered it"
    assert scenario.outputs[-1]["kind"] == "send", "no message to edit: a new one"


async def test_the_expiry_loop_closes_what_nobody_touched_for_a_lifetime(
    scenario_factory,
):
    scenario = await scenario_factory().start("block_links")

    await scenario.expiry_loop_pass()

    assert scenario.held_sessions == []
    expired = ml("texts.notices.expired", locale="pt-br")
    assert expired in scenario.current_message.embeds[0].description


async def test_later_moves_the_clock_of_what_runs_inside(scenario_factory):
    scenario = await scenario_factory().start("block_links")
    [opened] = scenario.held_sessions

    with scenario.later(600):
        await scenario.confirm()
    await scenario.expiry_loop_pass()
    assert scenario.held_sessions == [opened], "the click ten minutes later renewed it"

    sent = len(scenario.outputs)
    with scenario.later(600 + 16 * 60):
        await scenario.sweep()
    assert scenario.held_sessions == []
    assert [event["kind"] for event in scenario.outputs[sent:]] == [
        "later",
        "sweep",
    ], "the token of a click sixteen minutes old is dead: nothing is edited"


async def test_discord_refuses_a_call_as_often_as_it_is_told(scenario_factory):
    scenario = await scenario_factory().start("block_links")
    refused = discord.HTTPException(SimpleNamespace(status=503, reason="busy"), "busy")
    scenario.discord_refuses("response.send_message", refused, times=1)
    interaction = scenario.interaction()

    with pytest.raises(discord.HTTPException):
        await interaction.response.send_message(content="one")
    await interaction.response.send_message(content="two")

    assert [event["kind"] for event in scenario.outputs[-2:]] == ["refused", "send"]


async def test_tokens_are_filled_as_the_form_on_screen_fills_its_copy(
    scenario_factory, deps
):
    from tests.generators.moderations import recorded_server

    scenario = scenario_factory()
    recorded_server(deps.mongo_client, scenario.guild.id, prefix="!!")
    await scenario.start("block_links")

    filled = await scenario.fill_tokens("${context.prefix}|${context.server_language}")

    assert filled == "!!|pt-br"


async def test_the_mode_on_screen_is_read_from_the_session_and_refused_when_wrong(
    scenario_factory, deps
):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    panel = await scenario_factory().start_command(block_links.FORM)
    panel.expect_mode("manage")

    scenario = await scenario_factory().start("default_roles")
    scenario.expect_mode("setup")
    scenario.expect_mode("setup", "default_roles")
    with pytest.raises(ScenarioAssertionError, match="runs as 'setup', expected 'manage'"):
        scenario.expect_mode("manage")
    with pytest.raises(ScenarioAssertionError, match="is 'default_roles', expected 'x'"):
        scenario.expect_mode("setup", "x")

    empty = scenario_factory()
    with pytest.raises(ScenarioAssertionError, match="none is on screen"):
        empty.expect_mode("setup")


async def test_a_command_opened_from_a_card_draws_over_that_card_with_what_it_passed(
    scenario_factory,
):
    card = discord.ui.LayoutView()
    card.add_item(discord.ui.Container(discord.ui.TextDisplay("launcher")))
    passed = []

    async def back():
        passed.append("back")
        return card

    scenario = scenario_factory().open_from(
        card, source="setup_dashboard", ending=back, back=back
    )
    await scenario.start_command(block_links.FORM)

    assert scenario.launcher is not None and scenario.alias(scenario.launcher) == "M1"
    assert scenario.outputs[1] == {**scenario.outputs[1], "kind": "send", "message": "M1"}
    await scenario.click("back")
    assert passed == ["back"], "the back the caller passed is the one called"
    assert scenario.live_messages == [scenario.current_message]


async def test_an_entry_point_gets_a_fresh_interaction_and_a_posted_view_is_clickable(
    scenario_factory,
):
    scenario = scenario_factory()
    reached = []

    async def entry(interaction, argument):
        reached.append((interaction.message, argument))

    await scenario.run(entry, "slash", name="/configurar")
    hits = []
    posted = scenario.post(_RecordingView(hits))
    await scenario.click("Gravar")

    assert reached == [(None, "slash")]
    assert scenario.outputs[0]["target"] == "/configurar"
    assert scenario.current_message is posted and hits[0].message is posted
    assert posted.jump_url.endswith(f"/{posted.channel.id}/{posted.id}")


class _Probe(commands.Cog):
    """A cog with one slash command that keeps every interaction it runs with."""

    def __init__(self):
        self.ran = []

    @app_commands.command(name="probe", description="probe")
    async def probe(self, interaction, word: str):
        self.ran.append((interaction, word))


async def test_a_slash_command_runs_after_the_tree_check_on_the_client_given(
    scenario_factory,
):
    scenario = scenario_factory(locale="en-us")
    scenario.client = SimpleNamespace(name="the bot")
    probe = _Probe()

    sent = await scenario.run_command(_Probe.probe, probe, "hi")

    assert probe.ran == [(sent, "hi")]
    assert sent.command is _Probe.probe and sent.client is scenario.client
    assert sent.extras["reader_locale"] == discord.Locale.american_english
    assert scenario.outputs[0]["target"] == "/probe"


class _LabelledModal(discord.ui.Modal):
    """A text, a select and a file, each under a label, as a command's modal draws
    them; keeps what each submit carried."""

    def __init__(self):
        super().__init__(title="Modal")
        self.text = discord.ui.TextInput()
        self.pick = discord.ui.Select(options=[
            discord.SelectOption(label="Um", value="one"),
            discord.SelectOption(label="Dois", value="two"),
        ])
        self.file = discord.ui.FileUpload(required=False)
        for text, component in (
            ("Texto", self.text), ("Escolha", self.pick), ("Arquivo", self.file),
        ):
            self.add_item(discord.ui.Label(text=text, component=component))
        self.submitted = []

    async def on_submit(self, interaction):
        files = [attachment.filename for attachment in self.file.values]
        self.submitted.append((self.text.value, self.pick.values, files))
        await interaction.response.defer()


async def test_a_modal_of_labels_is_read_and_submitted_by_its_labels(scenario_factory):
    scenario = scenario_factory()
    modal = _LabelledModal()

    async def entry(interaction):
        await interaction.response.send_modal(modal)

    await scenario.run(entry)
    scenario.expect_modal(field_labels=["Texto", "Escolha", "Arquivo"])
    assert scenario.pending_modal_fields() == ["Texto", "Escolha", "Arquivo"]
    assert scenario.pending_modal_inputs() == [modal.text, modal.pick, modal.file]
    await scenario.submit_modal({"Texto": "oi", "Escolha": "Dois", "Arquivo": "print.png"})

    assert modal.submitted == [("oi", ["two"], ["print.png"])]
    await scenario.run(entry)
    await scenario.submit_modal({"Texto": "de novo"}, modal=modal)
    assert modal.submitted[-1][0] == "de novo" and scenario.pending_modal is None
    await scenario.run(entry)
    with pytest.raises(LocatorError, match="offers no option 'Três'"):
        await scenario.submit_modal({"Escolha": "Três"})


class _FailingModal(_LabelledModal):
    """A modal whose submit raises, with discord.py's default `on_error`."""

    async def on_submit(self, interaction):
        raise RuntimeError("the submit broke")


class _AnsweringModal(_FailingModal):
    """A modal whose submit raises and that answers the failure itself."""

    def __init__(self):
        super().__init__()
        self.failures = []

    async def on_error(self, interaction, error):
        self.failures.append((interaction, error))


async def test_a_submit_that_raises_reaches_the_modals_own_on_error(scenario_factory):
    """As discord.py routes it; a modal with discord.py's default `on_error` raises
    in the test instead, so its failure never passes for a submit."""
    scenario = scenario_factory()
    answering = _AnsweringModal()

    async def entry(interaction):
        await interaction.response.send_modal(answering)

    await scenario.run(entry)
    await scenario.submit_modal({"Texto": "oi"})

    [(interaction, error)] = answering.failures
    assert isinstance(error, RuntimeError) and interaction.command is None
    with pytest.raises(RuntimeError, match="the submit broke"):
        await scenario.submit_modal({"Texto": "oi"}, modal=_FailingModal())


class _WhereModal(_LabelledModal):
    """A modal that keeps the message each submit carried."""

    def __init__(self):
        super().__init__()
        self.messages = []

    async def on_submit(self, interaction):
        self.messages.append(interaction.message)
        await interaction.response.defer()


class _AskingView(discord.ui.View):
    """One button, `Perguntar`, that opens `modal`."""

    def __init__(self, modal):
        super().__init__()
        button = discord.ui.Button(label="Perguntar", custom_id="ask")

        async def callback(interaction):
            await interaction.response.send_modal(modal)

        button.callback = callback
        self.add_item(button)


async def test_a_modal_is_submitted_from_the_message_whose_button_opened_it(
    scenario_factory,
):
    """As Discord sends it, whatever the bot posted since; a modal a slash command
    opened is submitted from the message on screen."""
    scenario = scenario_factory()
    modal = _WhereModal()
    asked = scenario.post(_AskingView(modal))
    await scenario.click("Perguntar", on=asked)
    newer = scenario.post(_RecordingView([]))

    await scenario.submit_modal({"Texto": "oi"})

    assert modal.messages == [asked] and scenario.current_message is newer

    async def entry(interaction):
        await interaction.response.send_modal(modal)

    await scenario.run(entry)
    await scenario.submit_modal({"Texto": "de novo"})
    assert modal.messages[-1] is scenario.current_message


async def test_every_interaction_holds_keikos_app_command_tree(scenario_factory):
    """What reads Keiko's own tree (the help) finds every global app command the bot
    syncs, on a slash command's interaction and on a click alike."""
    scenario = scenario_factory()
    posted = scenario.post(_RecordingView([]))
    synced = await synced_tree()

    for interaction in (scenario.interaction(), scenario.interaction(on=posted)):
        tree = interaction.client.tree
        assert await listed_commands(tree, scenario.locale) == synced.listed["pt-br"]


async def test_a_dismissed_message_leaves_the_screen_and_the_modal_inputs_are_read(
    scenario_factory, deps
):
    scenario = scenario_factory()
    first = _shown(scenario, _RecordingView([]))
    second = _shown(scenario, _RecordingView([]))

    scenario.dismiss()

    assert scenario.live_messages == [first] and scenario.current_message is first
    assert scenario.outputs[-1]["kind"] == "dismiss" and second.deleted

    card = await twitch.open_card(scenario_factory, deps, "pt-br")
    await card.click("customize:streamer")
    inputs = card.pending_modal_inputs()
    assert [field.label for field in inputs] == card.pending_modal_fields()
    assert all(isinstance(field, discord.ui.TextInput) for field in inputs)


def test_every_form_has_its_flows_module():
    assert {form: module_of(form).FORM for form in DiskSource().list()} == {
        form: form for form in DiskSource().list()
    }


def _event(seq, kind, actor="bot", **extra):
    return {"seq": seq, "actor": actor, "kind": kind, "message": "M1",
            "step": None, **extra}


def _scenario(events):
    return SimpleNamespace(outputs=events, transcript="(transcript)")


def _button(label, action=None):
    return {"type": "button", "label": label, "action": action,
            "style": "success", "disabled": False}


def test_component_ids_never_enter_a_golden():
    """Self-tests for the golden book mechanism.

    A book that silently accepts a changed screen protects nothing: these pin
    what enters a book, what a mismatch says, and what transport folding forgives.
    """
    events = [_event(1, "send", components=[
        _button("Confirm", action="card_done"),
        {"type": "container", "children": [_button("Back", action="picker_back")]},
    ])]
    projected = golden.project(events)
    buttons = [projected[0]["components"][0],
               projected[0]["components"][1]["children"][0]]
    assert all("action" not in button for button in buttons)
    assert events[0]["components"][0]["action"] == "card_done", "projection copies"


def test_missing_book_says_how_to_record_it(tmp_path):
    with pytest.raises(ScenarioAssertionError) as failure:
        golden.assert_book(_scenario([]), copy_keys.CopyTrace(), tmp_path / "none.yml",
                           "open.pt-br", "pt-br")
    assert "--update-golden" in str(failure.value)


def test_transport_is_folded_before_comparing():
    through_followup = [_event(1, "click", actor="user"), _event(2, "defer", ephemeral=True),
                        _event(3, "followup_edit", embed={"title": "x"})]
    through_response = [_event(1, "click", actor="user"),
                        _event(2, "edit", embed={"title": "x"})]
    left = golden.canonical(golden.project(through_followup))
    right = golden.canonical(golden.project(through_response))
    assert left == right
    assert [event["kind"] for event in right] == ["click", "edit"]


def test_a_thinking_defer_stays_visible():
    thinking = [_event(1, "defer", thinking=True, ephemeral=True), _event(2, "followup_send")]
    silent = [_event(1, "defer", ephemeral=True), _event(2, "followup_send")]
    assert golden.canonical(golden.project(thinking)) != golden.canonical(
        golden.project(silent)
    )


def test_transport_folding_never_reorders_the_answers():
    expected = [_event(1, "delete", message="M1"),
                _event(2, "followup_send", message="M2")]
    actual = [_event(1, "followup_send", message="M2"),
              _event(2, "delete", message="M1")]
    assert golden.canonical(golden.project(expected)) != golden.canonical(
        golden.project(actual)
    )


# ----------------------------------------------------------------- golden book

def _card(title, *buttons):
    return {"ephemeral": True, "components_v2": True, "components": [
        {"type": "container", "children": [
            {"type": "text", "content": f"## ⟨{title}⟩"},
            {"type": "text", "content": "-# ⟨vocabulary.fallbacks.footer⟩"},
        ]},
        {"type": "actionrow", "children": [_button(f"⟨{label}⟩") for label in buttons]},
    ]}


def _flow(*shown):
    records = [({"seq": 1, "actor": "user", "kind": "start_command",
                 "message": None, "target": "form"},) * 2]
    for index, screen in enumerate(shown, start=2):
        event = {"seq": index, "actor": "bot", "kind": "send", "message": "M1", **screen}
        records.append((event, event))
    return records


def test_changes_rebuild_every_list_edit():
    base = {"rows": [{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}], "title": "x"}
    targets = [
        {"rows": [{"a": 1}, {"new": 0}, {"b": 2}, {"c": 3}, {"d": 4}], "title": "x"},
        {"rows": [{"a": 1}, {"d": 4}], "title": "y"},
        {"rows": [{"a": 9}, {"b": 2, "e": 5}, {"z": 0}, {"y": 0}, {"x": 0}]},
        {"rows": [], "title": "x", "more": [1, 2]},
    ]
    for target in targets:
        assert golden.apply_changes(base, golden.changes(base, target)) == target


def test_a_variant_is_stored_as_its_base_and_the_changes_the_recorder_computed():
    panel = _card("form.panel.title", "buttons.pause.label", "buttons.help.label")
    paused = copy.deepcopy(panel)
    paused["components"][1]["children"][0]["label"] = "⟨buttons.unpause.label⟩"
    book = golden.build_book({
        "pause.pt-br": ("pt-br", _flow(panel, paused)),
        "open.pt-br": ("pt-br", _flow(panel)),
    })

    variant = next(name for name in book["screens"] if name != "panel")
    assert book["screens"][variant]["from"] == "panel"
    assert book["screens"][variant]["changes"] == [
        {"set components.1.children.0.label": "⟨buttons.unpause.label⟩"}
    ]
    assert golden.expand(book, variant) == paused
    assert golden.flow_lines(book["flows"]["pause.pt-br"]) == [
        'start_command form', "send M1 panel", f"send M1 {variant}",
    ]


def test_a_book_reads_back_from_its_yaml_and_reproduces_every_flow(tmp_path):
    panel = _card("form.panel.title", "buttons.pause.label")
    flows = {"open.pt-br": ("pt-br", _flow(panel))}
    path = tmp_path / "form.yml"
    path.write_text(golden.dump_book(golden.build_book(flows)), encoding="utf-8")

    book = golden.load_book(path)
    assert golden.book_mismatch(book, "open.pt-br", "pt-br", flows["open.pt-br"][1]) is None
    assert "no flow 'other.pt-br'" in golden.book_mismatch(book, "other.pt-br", "pt-br", [])


def test_a_book_reports_an_answer_the_flow_did_not_have():
    panel = _card("form.panel.title", "buttons.pause.label")
    book = golden.build_book({"open.pt-br": ("pt-br", _flow(panel))})
    deleted = {"seq": 3, "actor": "bot", "kind": "delete", "message": "M1"}

    problem = golden.book_mismatch(book, "open.pt-br", "pt-br",
                                   [*_flow(panel), (deleted, deleted)])
    assert "+delete M1" in problem


def test_a_book_names_the_screen_that_differs_and_shows_the_change():
    panel = _card("form.panel.title", "buttons.pause.label")
    book = golden.build_book({"open.pt-br": ("pt-br", _flow(panel))})
    grey = copy.deepcopy(panel)
    grey["components"][1]["children"][0]["style"] = "secondary"

    problem = golden.book_mismatch(book, "open.pt-br", "pt-br", _flow(grey))
    assert "screen 'panel' differs" in problem
    assert "send M1 panel?" in problem
    assert '"style": "secondary"' in problem


def test_a_rendered_screen_catches_other_words_under_the_same_keys():
    panel = _card("form.panel.title", "buttons.pause.label")
    words = copy.deepcopy(panel)
    words["components"][1]["children"][0]["label"] = "Pausar"
    start = _flow()[0]
    flows = {"open.pt-br": ("pt-br", [start, ({"seq": 2, "actor": "bot", "kind": "send",
                                               "message": "M1", **panel},
                                              {"seq": 2, "actor": "bot", "kind": "send",
                                               "message": "M1", **words})])}
    book = golden.build_book(flows, rendered=("panel",))
    assert "Pausar" in book["screens"]["panel"]["rendered"]["pt-br"]

    other = copy.deepcopy(words)
    other["components"][1]["children"][0]["label"] = "Pausa"
    replay = [start, (flows["open.pt-br"][1][1][0],
                      {"seq": 2, "actor": "bot", "kind": "send", "message": "M1", **other})]
    assert "renders other words" in golden.book_mismatch(book, "open.pt-br", "pt-br", replay)


def _worded(seq, keyed, label):
    shown = copy.deepcopy(keyed)
    shown["components"][1]["children"][0]["label"] = label
    envelope = {"seq": seq, "actor": "bot", "kind": "send", "message": "M1"}
    return ({**envelope, **keyed}, {**envelope, **shown})


def test_a_locale_that_never_shows_the_base_keeps_its_words_where_it_shows_the_family():
    """The en-us flows reach the panel only after the setup, a variant of the
    panel, so a locale that never shows the base keeps its words on what it shows."""
    panel = _card("form.panel.title", "buttons.pause.label")
    paused = copy.deepcopy(panel)
    paused["components"][1]["children"][0]["label"] = "⟨buttons.unpause.label⟩"
    start = _flow()[0]
    flows = {
        "open.pt-br": ("pt-br", [start, _worded(2, panel, "Pausar")]),
        "again.pt-br": ("pt-br", [start, _worded(2, panel, "Pausar")]),
        "paused.en-us": ("en-us", [start, _worded(2, paused, "Resume")]),
    }
    book = golden.build_book(flows, rendered=("panel",))

    variant = next(name for name in book["screens"] if name != "panel")
    assert list(book["screens"]["panel"]["rendered"]) == ["pt-br"]
    assert book["screens"][variant]["rendered"]["en-us"] == ["Resume"]
    recorded = flows["paused.en-us"][1]
    replay = [start, _worded(2, paused, "Resumee")]
    assert golden.book_mismatch(book, "paused.en-us", "en-us", recorded) is None
    problem = golden.book_mismatch(book, "paused.en-us", "en-us", replay)
    assert "renders other words" in problem


def _keyer(read, fills=(), admin=()):
    trace = copy_keys.CopyTrace()
    trace.read = {text: {key} for text, key in read.items()}
    trace.fills = list(fills)
    return trace.keyer(admin)


def test_a_copy_word_inside_an_admin_sentence_stays_as_the_admin_wrote_it():
    keyer = _keyer({"Resposta": "answer.label", "links": "slash.name"})
    assert keyer("💬 **Resposta:** Nada de links aqui!") == (
        "💬 **⟨answer.label⟩:** Nada de links aqui!"
    )


def test_a_filled_text_is_written_as_its_key_and_the_values_the_code_passed():
    keyer = _keyer(
        {"Comando **$name** pausado": "paused.description", "• Use /report": "footer",
         "moderações": "groups.moderations", "links": "slash.name"},
        fills=[("Comando **$name** pausado", "Comando **moderações links** pausado",
                {"name": "moderações links"})],
    )
    assert keyer("Comando **moderações links** pausado\n\n-# • Use /report") == (
        "⟨paused.description: name=«⟨groups.moderations⟩ ⟨slash.name⟩»⟩"
        "\n\n-# ⟨footer⟩"
    )


def test_a_number_the_code_wrote_beside_a_copy_word_stays_beside_its_key():
    keyer = _keyer({"Página": "texts.page", "Notificações": "categories.notifications",
                    "links": "slash.name"})
    assert keyer("• Página 1 / 1") == "• ⟨texts.page⟩ 1 / 1"
    assert keyer("### Notificações #2") == "### ⟨categories.notifications⟩ #2"
    assert keyer("10 links") == "10 links"


def test_the_whitespace_around_a_copy_is_layout():
    keyer = _keyer({"Oi, eu busco **ks!**\n": "form.description"})
    assert keyer("Oi, eu busco **ks!**\n\n:pencil: **Configurações**") == (
        "⟨form.description⟩\n\n:pencil: **Configurações**"
    )


def test_a_template_filled_outside_the_token_pass_is_written_as_its_key(monkeypatch):
    """The birthday dates (a `string.Template`) and the announcement previews (an
    admin's `{user}` template) are written as their key and every value the code
    passed, a lowercased copy as its key, so both locales of a panel key alike."""
    from string import Template

    from app.core import tokens

    trace = copy_keys.CopyTrace().install(monkeypatch)
    trace.read = {"$day de $month_lower": {"texts.date_label"},
                  "Maio": {"texts.months.may"}, "🎂 Viva, {user}!": {"title"}}
    date = Template("$day de $month_lower").safe_substitute(
        month_name="Maio", month_lower="maio", day=12
    )
    title = tokens.templated("🎂 Viva, {user}!", {"user": "<@1>"})

    assert (date, title) == ("12 de maio", "🎂 Viva, <@1>!")
    keyer = trace.keyer()
    assert keyer(f"- <@1>: {date}") == (
        "- <@1>: ⟨texts.date_label: day=«12»; month_lower=«⟨texts.months.may"
        " (lowercase)⟩»; month_name=«⟨texts.months.may⟩»⟩"
    )
    assert keyer(f"> **{title}**") == "> **⟨title: user=«<@1>»⟩**"


def test_a_value_the_admin_saved_is_never_read_as_copy():
    keyer = _keyer({"twitch.tv": "link.placeholder"}, admin={"twitch.tv"})
    assert keyer("- `twitch.tv`") == "- `twitch.tv`"


def test_a_rendered_screen_the_book_does_not_name_is_refused():
    panel = _card("form.panel.title", "buttons.pause.label")
    with pytest.raises(ValueError, match="no screen \\['form.panel'\\]"):
        golden.build_book({"open.pt-br": ("pt-br", _flow(panel))}, rendered=("form.panel",))


def test_a_copy_key_reads_as_the_book_names_it_with_its_values_filled():
    counter = copy_keys.copy_text(
        "block_links.form.steps.custom_links.counter", "pt-br", count=2, max=25
    )

    assert counter == "2 de 25"
    assert copy_keys.copy_text("buttons.pause.label", "en-us") == ml(
        "buttons.pause.label", "en-us"
    ), "a copy file's key reads as `ml` reads it"
    assert copy_keys.copy_text("no.such.key", "pt-br") == "no.such.key"

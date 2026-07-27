"""Self-tests for the behavioral harness.

A broken scenario runner must not silently produce passing tests: these
cover the interaction state machine, click dispatch across the repo's four
callback patterns, normalization determinism, transcript diagnostics, and
scenario isolation.
"""
import discord
import pytest

from tests.behavioral.harness.driver import FormScenario
from tests.behavioral.harness.errors import (
    HarnessProtocolError,
    LocatorError,
    ScenarioAssertionError,
)
from tests.behavioral.harness.fake_interaction import FakeInteraction
from tests.behavioral.harness.message_store import MessageStore
from tests.behavioral.harness import locators
from tests.mocks.discord import create_guild, create_member

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


async def test_editing_deleted_message_raises():
    interaction = _interaction()
    await interaction.response.defer()
    message = await interaction.followup.send(content="x")
    await interaction.followup.delete_message(message.id)
    with pytest.raises(HarnessProtocolError):
        await interaction.followup.edit_message(message.id, content="y")


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
    await scenario.select_option("Member", target="Cargos para Membros...")
    await scenario.confirm()
    await scenario.confirm()
    return scenario


async def test_two_identical_runs_produce_identical_normalized_output(
        scenario_factory, deps):
    first = await _mini_run(scenario_factory)
    deps.mongo_client.guild["default_roles"]._data.clear()
    deps.mongo_client.guild["moderations"]._data.clear()
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
    scenario = await scenario_factory().start("block_links")
    await scenario.confirm()             # -> multi_select
    await scenario.confirm()             # -> options
    await scenario.click("option:Youtube")
    await scenario.confirm()             # -> modal step
    scenario.expect_modal()
    with pytest.raises(LocatorError):
        await scenario.submit_modal({"campo-que-nao-existe": "x"})


# ------------------------------------------------------------------- isolation

async def test_scenarios_do_not_share_state(scenario_factory):
    first = await scenario_factory().start("default_roles")
    second = await scenario_factory().start("block_links")

    assert first.store is not second.store
    assert first.form_view is not second.form_view
    assert first.outputs[0]["target"] == "default_roles"
    assert second.outputs[0]["target"] == "block_links"

"""Forms nobody finishes close on their own.

Found while planning the log changes: `Runtime.expire_stale` had no caller in
production. A session nobody finished kept its log message "in progress"
forever, its buttons stayed on screen until someone clicked them, and every
session, open or closed, stayed in memory for the life of the process.

Guaranteed here: the events cog runs the sweep on a loop that survives a failing
pass; a pass takes the buttons off an abandoned form and closes its story as
abandoned without posting engine lines; and sessions that ended are forgotten.
"""
import logging

import pytest

from app import logger as logger_module
from app.constants import ViewConstants
from app.forms.discord.views import ReopenButton
from app.core import journey
from app.core import trace as trace_service
from tests.behavioral.harness.driver import FormScenario
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]


@pytest.fixture
def scenario(deps):
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    return FormScenario(guild=guild, user=user, locale="pt-br", mongo=deps.mongo_client)


@pytest.fixture
def closed_traces():
    trace_service.clear_sinks()
    captured = []
    trace_service.register_sink(captured.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    yield captured
    logger_module.logger.removeHandler(folding)
    trace_service.clear_sinks()


@pytest.fixture
def stories():
    journey.clear()
    seen = {}
    journey.set_publisher(lambda story: seen.__setitem__(story.session_id, story))
    journey.install()
    yield seen
    journey.clear()
    journey.set_publisher(None)


def test_the_events_cog_sweeps_forms_on_a_loop():
    from app.core import events as events_module

    assert events_module.Events.sweep_forms.seconds == ViewConstants.FORM_SWEEP_SECONDS


async def test_one_pass_of_the_loop_runs_the_runtime_sweep(scenario):
    await scenario.start_command("default_roles")

    await scenario.expiry_loop_pass()

    [reopen] = scenario.current_message.view.children
    assert isinstance(reopen, ReopenButton), "the pass took the buttons off"
    assert scenario.held_sessions == [], "and forgot the form"


async def test_a_failing_pass_does_not_stop_the_loop(scenario, caplog):
    await scenario.start_command("default_roles")
    scenario.discord_refuses("followup.edit_message", ConnectionResetError("boom"))

    with caplog.at_level(logging.WARNING):
        await scenario.expiry_loop_pass()

    assert any("Form sweep failed" in record.getMessage() for record in caplog.records)


async def test_a_sweep_closes_an_abandoned_form_and_its_story_without_engine_messages(
    scenario, closed_traces, stories
):
    await scenario.start_command("default_roles")

    await scenario.sweep()

    [reopen] = scenario.current_message.view.children
    assert isinstance(reopen, ReopenButton), "the buttons leave, but Abrir de novo"
    assert [story.result for story in stories.values()] == ["abandoned"]
    assert not [trace for trace in closed_traces if trace.is_noteworthy], (
        "expiring is bookkeeping, not a message of engine lines"
    )


async def test_a_sweep_forgets_the_sessions_that_ended(scenario):
    await scenario.start_command("default_roles")

    await scenario.sweep()

    assert scenario.sessions == []
    assert scenario.held_sessions == [], "the adapter forgets its half too"

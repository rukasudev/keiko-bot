"""A command that fails still says, on its log message, which run of the day it was.

Reported in the v1 review of the loop and runtime PR: once the attempt counter moved to
a thread, the "Nth run" footnote was written only after the command returned, so a
command that raised, the one whose message is read for that footnote, lost it, and
the count was left running with nobody waiting on it.

Shared behaviour: `keiko_command` (every slash command) and `run_feature_command`
(every feature button), through `analytics.counting_attempt`. Exposed by: the review of
the commit that moved the counter off the loop.

Guaranteed: whether the command finished or raised, its trace carries the footnote of
its attempt, and the count is awaited before the trace closes.
"""
from types import SimpleNamespace

import discord
import pytest

from app.decorators import keiko_command
from app.services import analytics
from app.services.trace import current_trace
from tests.mocks.discord import MockInteraction, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

SECOND_RUN = analytics.describe_attempt(2, "block_links")


class Failing:
    """A slash command of a feature that raises, as a broken one does."""

    traces = []

    @keiko_command(name="failing", description="Keiko fails on purpose")
    async def failing(self, interaction: discord.Interaction) -> None:
        Failing.traces.append(current_trace())
        raise RuntimeError("the feature broke")


@pytest.fixture
def second_run(monkeypatch):
    monkeypatch.setattr(analytics, "count_attempt", lambda guild_id, feature: 2)


def interaction_in(guild, command=None):
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.command = command
    interaction.client = SimpleNamespace(app_commands=[])
    return interaction


async def test_a_slash_command_that_raises_keeps_its_footnote(deps, guild, second_run, monkeypatch):
    monkeypatch.setattr(Failing, "traces", [])
    interaction = interaction_in(
        guild, SimpleNamespace(qualified_name="moderations block links", _attr="block_links")
    )

    with pytest.raises(RuntimeError):
        await Failing.failing.callback(SimpleNamespace(), interaction)

    [trace] = Failing.traces
    assert trace.footnote == SECOND_RUN


async def test_a_feature_button_that_raises_keeps_its_footnote(deps, guild, second_run, monkeypatch):
    from app.components import buttons

    traces = []

    async def open_feature(interaction, command_key, source):
        traces.append(current_trace())
        raise RuntimeError("the feature broke")

    monkeypatch.setattr(buttons, "increment_redis_key", lambda key: 1)
    monkeypatch.setattr("app.settings.open_feature", open_feature)

    with pytest.raises(RuntimeError):
        await buttons.run_feature_command(interaction_in(guild), "block_links", "setup_dashboard")

    [trace] = traces
    assert trace.footnote == SECOND_RUN

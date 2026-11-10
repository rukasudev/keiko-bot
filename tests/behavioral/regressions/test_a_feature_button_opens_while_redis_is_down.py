"""A feature button opens its feature whether or not Redis answers its counter.

Broke as: `run_feature_command` counted the click (`increment_redis_key`, one
Redis call in a thread) before `open_feature`, with nothing around it. During a
Redis outage every feature button waited for the socket timeout, up to five
seconds, past Discord's three-second answer window, and then raised, so the
feature never opened.

Shared behaviour: `run_feature_command` (app/components/buttons.py), the single
entry from any button into a feature: the /setup cards, the greeting, /help and
the activation panels.

Guaranteed: the feature opens before the counter is answered, a counter that
fails never fails the click, and the count is still made when Redis answers.
"""
import threading
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.constants import LogTypes
from tests.mocks.discord import MockInteraction, create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

COUNTER = f"{LogTypes.COMMAND_CALL_TYPE}:block_links:button"


def interaction_in(guild):
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.client = SimpleNamespace(app_commands=[])
    return interaction


@pytest.fixture
def opened(monkeypatch):
    seen = []

    async def open_feature(interaction, command_key, source, **opening):
        seen.append(command_key)
        await interaction.response.send_message(command_key)

    monkeypatch.setattr("app.settings.open_feature", open_feature)
    return seen


async def test_a_feature_button_opens_before_a_redis_that_does_not_answer(
    deps, monkeypatch
):
    from app.components import buttons

    happened = []
    feature_open = threading.Event()

    def timing_out(key, amount=1):
        feature_open.wait(timeout=1)
        happened.append("count timed out")
        raise RedisConnectionError("Timeout reading from redis")

    monkeypatch.setattr(deps.redis_client, "incrby", timing_out)
    interaction = interaction_in(create_guild())

    async def open_feature(interaction, command_key, source, **opening):
        happened.append("feature opened")
        feature_open.set()
        await interaction.response.send_message(command_key)

    monkeypatch.setattr("app.settings.open_feature", open_feature)

    await buttons.run_feature_command(interaction, "block_links", "setup_dashboard")

    assert happened[0] == "feature opened", (
        f"the button waited on Redis before opening the feature: {happened}"
    )
    interaction.assert_response_sent("send_message")


async def test_a_feature_button_still_counts_the_click(deps, opened):
    from app.components import buttons

    await buttons.run_feature_command(
        interaction_in(create_guild()), "block_links", "setup_dashboard"
    )

    assert opened == ["block_links"]
    assert deps.redis_client.get(COUNTER) == "1"

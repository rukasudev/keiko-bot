"""An error a person got past gets a check on its log message.

On the local bot (2026-09-14) a click in the welcome setup failed with 10062
Unknown interaction and the error channel showed a Command Error. The person
clicked again, the stale notice redrew the screen and the setup went on, but
nothing in the channel said so: an admin reading it could not tell a stuck
person from one who carried on.

On 2026-09-15 pausing from the welcome panel failed after saving; Lucas opened
the command again and carried on, but a new command is a new session, so no
error got its check.

Shared behaviour: `DiscordLogsHandler`, `observability.log_effects`, the journey
hooks. Consumers that exposed it: the welcome_messages setup and panel.
Guaranteed: a form error gets one check once the same person uses the same
feature in the same server without a failure, in that session or a new one; an
error nobody got past keeps none, and a pending error is forgotten after a window.
"""
import asyncio
import logging
from types import SimpleNamespace

import discord
import pytest

from app import logger as logger_module
from app.services import journey as journey_service
from app.services import trace as trace_service
from tests.behavioral.harness import locators
from tests.behavioral.harness.driver import FormScenario
from tests.behavioral.harness.fake_interaction import FakeResponse
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("logging")]


class FakeLogMessage:
    def __init__(self, embed):
        self.embed = embed
        self.reactions = []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def edit(self, **kwargs):
        return None


class FakeLogChannel:
    def __init__(self):
        self.messages = []

    async def send(self, **kwargs):
        message = FakeLogMessage(kwargs.get("embed"))
        self.messages.append(message)
        return message


@pytest.fixture
async def log_channel(monkeypatch):
    channel = FakeLogChannel()
    bot = SimpleNamespace(
        loop=asyncio.get_running_loop(),
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=lambda _channel_id: channel,
    )
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)
    monkeypatch.setattr(trace_service, "_sinks", list(trace_service._sinks))
    monkeypatch.setattr(journey_service, "_PUBLISHER", journey_service._PUBLISHER)
    monkeypatch.setattr(journey_service, "_RECOVERY_LISTENER", None, raising=False)
    handler = logger_module.DiscordLogsHandler(bot)
    yield channel
    root.removeHandler(handler)
    root.setLevel(previous_level)


def _render_errors(channel):
    return [
        message
        for message in channel.messages
        if message.embed is not None and "Render failed" in (message.embed.description or "")
    ]


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def _a_click_that_fails(deps, monkeypatch, guild=None, user=None):
    guild = guild or create_guild()
    user = user or create_member(guild, id=555, name="Tester")
    scenario = await FormScenario(
        guild=guild, user=user, locale="pt-br", mongo=deps.mongo_client
    ).start_command("default_roles")
    message = scenario.current_message
    button = locators.find_button(message, "continue", scenario.locale)
    original = FakeResponse.edit_message
    failed = []

    async def fails_once(self, **kwargs):
        if not failed:
            failed.append(True)
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"),
                {"code": 10062, "message": "Unknown interaction"},
            )
        return await original(self, **kwargs)

    monkeypatch.setattr(FakeResponse, "edit_message", fails_once)
    await button.callback(scenario._mint(message=message, custom_id=button.custom_id))
    await _settle()
    return scenario, message, button


async def test_an_error_the_person_got_past_gets_a_check(deps, log_channel, monkeypatch):
    scenario, message, button = await _a_click_that_fails(deps, monkeypatch)
    [error] = _render_errors(log_channel)
    assert error.reactions == []

    await button.callback(scenario._mint(message=message, custom_id=button.custom_id))
    await _settle()

    assert error.reactions == ["✅"], "the person clicked again and the setup went on"


async def test_an_error_nobody_got_past_keeps_no_check(deps, log_channel, monkeypatch):
    await _a_click_that_fails(deps, monkeypatch)

    [error] = _render_errors(log_channel)
    assert error.reactions == []


async def test_the_check_is_added_once(deps, log_channel, monkeypatch):
    scenario, message, button = await _a_click_that_fails(deps, monkeypatch)
    await button.callback(scenario._mint(message=message, custom_id=button.custom_id))
    await _settle()
    await scenario.confirm()
    await _settle()

    [error] = _render_errors(log_channel)
    assert error.reactions == ["✅"]


async def _open(deps, guild, user, command_key):
    await FormScenario(
        guild=guild, user=user, locale="pt-br", mongo=deps.mongo_client
    ).start_command(command_key)
    await _settle()


async def test_an_error_is_checked_when_the_same_member_opens_the_feature_again(
    deps, log_channel, monkeypatch
):
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    await _a_click_that_fails(deps, monkeypatch, guild, user)
    [error] = _render_errors(log_channel)

    await _open(deps, guild, user, "default_roles")

    assert error.reactions == ["✅"], "a new command by the same person carried on"


async def test_an_error_is_not_checked_by_another_member_or_feature(
    deps, log_channel, monkeypatch
):
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    await _a_click_that_fails(deps, monkeypatch, guild, user)
    [error] = _render_errors(log_channel)

    await _open(deps, guild, create_member(guild, id=777, name="Other"), "default_roles")
    await _open(deps, guild, user, "block_links")

    assert error.reactions == []


async def test_a_pending_error_is_forgotten_after_the_window(
    deps, log_channel, monkeypatch
):
    from app.constants import Commands

    monkeypatch.setattr(Commands, "ANALYTICS_RECOVERY_WINDOW_SECONDS", 0, raising=False)
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    await _a_click_that_fails(deps, monkeypatch, guild, user)
    [error] = _render_errors(log_channel)

    await _open(deps, guild, user, "default_roles")

    assert error.reactions == []

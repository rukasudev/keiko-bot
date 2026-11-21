"""What Keiko logs and where it lands: a failed command reaches the error channel before
the person is answered, an error someone got past gets its check, the error channel
holds only Keiko's own errors and never what a member wrote, a log message stays under
Discord's limits, a server joining or leaving reaches the log channel, and a webhook
posts one message, only when something failed.
"""
import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import requests
from discord import app_commands
from flask import Flask
from prometheus_client import REGISTRY

import app as app_module
import app.integrations.http as webhooks_package
from app import logger as logger_module
from app.commands.block_links import service as block_links
from app.commands.setup import service as setup_service
from app.constants import DiscordLimits as limits
from app.constants import LogTypes as logconstants
from app.core import analytics, debug_logs
from app.core import errors as errors_module
from app.core import journey as journey_service
from app.core import trace as trace_service
from app.core.errors import Errors
from app.core.trace import current_trace
from app.data import data, wait
from app.decorators import keiko_command
from app.exceptions import ErrorContext
from app.forms.discord import callbacks
from app.integrations.http import schedule_webhook_job, webhooks
from app.logger import StoredLogsHandler
from tests.behavioral.harness.driver import FormScenario
from tests.behavioral.harness.fake_interaction import FakeResponse
from tests.generators.moderations import seed_cog
from tests.mocks.database import fake_store
from tests.mocks.discord import (
    MockInteraction,
    MockMember,
    MockMessage,
    create_guild,
    create_member,
    create_message,
)
from tests.mocks.web import respond

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("logging"),
]


def errors_cog():
    return Errors(SimpleNamespace(config=SimpleNamespace(is_prod=lambda: False)))


def failing_command():
    """A command interaction and the error discord.py hands to the tree's handler."""
    guild = create_guild()
    member = create_member(guild, id=777, name="Rukasu")
    scenario = FormScenario(guild=guild, user=member, locale="en-us")
    interaction = scenario.interaction()
    interaction.command = SimpleNamespace(
        qualified_name="moderations block links", _attr="block_links"
    )
    try:
        raise KeyError("allowed_links")
    except KeyError as original:
        error = app_commands.CommandInvokeError(SimpleNamespace(name="links"), original)
    return scenario, interaction, error


def expired():
    return discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"),
        {"code": 10062, "message": "Unknown interaction"},
    )


async def stored_failures(deps):
    """The `command.failed` interactions once the analytics writer wrote its batch."""
    await analytics.flush()
    return list(deps.mongo_client.events.interactions.find({"type": "command.failed"}))


def failed_events(analytics_events):
    return [event for event in analytics_events if event["type"] == "command.failed"]


def answered(scenario):
    return [
        event
        for event in scenario.outputs
        if event["actor"] == "bot"
        and event["kind"] in ("send", "followup_send")
        and ((event.get("embed") or {}).get("title") or "").startswith("🚨")
    ]


def _every_embed(log_channels):
    return (
        log_channels.logs.embeds
        + log_channels.errors.embeds
        + log_channels.calls.embeds
        + log_channels.actions.embeds
    )


@pytest.fixture
def stalled_mongo(deps, monkeypatch):
    """Mongo takes the connection and never answers the writes of the interactions."""
    released = asyncio.Event()

    async def insert_many(documents):
        await released.wait()

    monkeypatch.setattr(deps.data.events_interactions, "insert_many", insert_many)
    yield
    released.set()


async def test_a_failed_command_reaches_the_error_channel_the_audit_and_analytics(
    deps, log_channels, analytics_events
):
    """A command that fails is reported before the person hears about it.

    Broke on 2026-08-21 and stayed broken: `send_default_error_message` read `error`,
    a name it was never given, to emit `command.failed`. Every failing command
    answered the person and then raised a NameError, so nothing reached the error
    channel, `audit.errors` or analytics: the last `audit.errors` record in
    production is from August. Answering first had a second cost: when the answer
    itself failed (an interaction Discord had already expired, 10062), the failure
    was never recorded either.

    Shared behaviour: `Errors.on_app_command_error`, the error handler of the whole
    command tree. Consumer that exposed it: every slash command, read from the
    production `audit.errors` collection (`ruff --select F821` names the line).

    Found in review of the fix: the answer waited for the `audit.errors` write, a
    pymongo insert on the default executor with no write timeout, so during a Mongo
    stall a burst of failures held every worker of that executor, and everything
    else the bot runs through `to_thread` (block links reads it on every message)
    waited behind them; anything raised while recording skipped the answer; and
    a refused answer or a slow store posted a warning of its own beside the error.
    Found in the closing review: a failure while recording reached nothing in
    Discord, because the quiet trace kept its line and the exception surfaced only
    as asyncio's "Task exception was never retrieved", which is muted there.

    Database PR 2 folded `audit.errors` into `command.failed`: the failure is one
    interaction, written by the analytics writer, carrying the command and the error
    type and never the traceback, which stays in the error channel and the logs; the
    `command.error:*` counter the handler waited for went with it, so nothing is stored
    beside the event.

    Guaranteed: a failed command puts a message with its traceback in the error
    channel and a `command.failed` event carrying the error type before the person
    is answered, and that event is what the analytics writer stores, with no traceback; the
    person is answered even when the answer fails, while the record is still being
    written, and when recording itself
    raises, which reaches the error channel too; a Mongo stall holds no thread of
    the default executor; the handler adds one message, the error. (A failed
    `keiko_command` also posts its own trace in the calls channel: that message is
    the command's, not the handler's.)
    """
    scenario, interaction, error = failing_command()

    await errors_cog().on_app_command_error(interaction, error)

    [embed] = log_channels.errors.embeds
    assert "moderations block links" in embed.description
    assert "KeyError" in embed.description
    assert 'raise KeyError("allowed_links")' in embed.description, (
        "the traceback is what the error channel is read for"
    )
    [event] = failed_events(analytics_events)
    assert event["props"]["error_type"] == "KeyError"
    assert event["props"]["command"] == "moderations block links"
    assert event["cog"] == "block_links"
    [stored] = await stored_failures(deps)
    assert stored["cog"] == "block_links"
    assert stored["props"] == {
        "command": "moderations block links", "error_type": "KeyError"
    }
    assert "allowed_links" not in str(stored), "a traceback reached the event"
    [answer] = answered(scenario)
    assert answer["ephemeral"]


async def test_a_failure_is_recorded_even_when_the_answer_cannot_be_sent(
    deps, log_channels, analytics_events, monkeypatch
):
    async def refused(self, *args, **kwargs):
        raise expired()

    monkeypatch.setattr(FakeResponse, "send_message", refused)
    scenario, interaction, error = failing_command()

    await errors_cog().on_app_command_error(interaction, error)

    assert len(log_channels.errors.embeds) == 1
    assert len(await stored_failures(deps)) == 1
    assert len(failed_events(analytics_events)) == 1


async def test_the_error_channel_and_analytics_hear_of_a_failure_before_the_person(
    deps, log_channels, analytics_events, monkeypatch
):
    reported_when_answered = {}
    answer = FakeResponse.send_message

    async def answering(self, *args, **kwargs):
        reported_when_answered.update(
            error_channel=len(log_channels.errors.embeds),
            analytics=len(failed_events(analytics_events)),
        )
        return await answer(self, *args, **kwargs)

    monkeypatch.setattr(FakeResponse, "send_message", answering)
    scenario, interaction, error = failing_command()

    await errors_cog().on_app_command_error(interaction, error)

    assert reported_when_answered == {"error_channel": 1, "analytics": 1}
    assert len(await stored_failures(deps)) == 1, "and the record follows"


async def test_the_person_is_answered_while_the_record_is_still_being_written(
    deps, log_channels, stalled_mongo
):
    """Discord gives three seconds, and a failure late in them has none to spare."""
    scenario, interaction, error = failing_command()

    handling = asyncio.ensure_future(errors_cog().on_app_command_error(interaction, error))
    await asyncio.sleep(0.2)
    answered_while_writing = len(answered(scenario))
    await asyncio.wait_for(handling, timeout=3)

    assert answered_while_writing == 1, "the answer does not wait for the record"


async def test_a_store_that_never_returns_still_lets_the_answer_arrive(
    deps, log_channels, analytics_events, stalled_mongo
):
    scenario, interaction, error = failing_command()

    await asyncio.wait_for(errors_cog().on_app_command_error(interaction, error), timeout=2)

    assert len(answered(scenario)) == 1
    assert len(log_channels.errors.embeds) == 1, "the error channel does not wait"
    assert len(failed_events(analytics_events)) == 1, "nor does analytics"


async def test_a_burst_of_failures_during_a_mongo_stall_holds_no_other_threaded_work(
    deps, log_channels, stalled_mongo
):
    """More failures than the default executor has workers, while Mongo is stalled."""
    failures = [failing_command() for _ in range(40)]

    await asyncio.wait_for(
        asyncio.gather(
            *(errors_cog().on_app_command_error(interaction, error)
              for _, interaction, error in failures)
        ),
        timeout=5,
    )
    unrelated = asyncio.ensure_future(asyncio.to_thread(time.monotonic))
    done, _ = await asyncio.wait({unrelated}, timeout=1)

    assert done, "a thread call unrelated to the failures waited behind the stalled writes"
    assert all(len(answered(scenario)) == 1 for scenario, _, _ in failures)


@pytest.mark.parametrize("trouble", ["refused answer", "slow store"])
async def test_the_handler_adds_one_message_for_a_failed_command(
    deps, log_channels, monkeypatch, request, trouble
):
    """The handler adds one message, the error: its warnings belong to its trace."""
    if trouble == "refused answer":
        async def refused(self, *args, **kwargs):
            raise expired()

        monkeypatch.setattr(FakeResponse, "send_message", refused)
    else:
        request.getfixturevalue("stalled_mongo")
    scenario, interaction, error = failing_command()

    await errors_cog().on_app_command_error(interaction, error)

    assert [embed.title for embed in _every_embed(log_channels)] == ["❌ Command Error"]
    assert len(log_channels.errors.embeds) == 1, "and it is in the error channel"


async def test_a_failure_while_recording_still_answers_and_reaches_the_error_channel(
    deps, log_channels, monkeypatch
):
    def broken(**kwargs):
        raise RuntimeError("the record could not be built")

    monkeypatch.setattr(errors_module, "ErrorContext", SimpleNamespace(from_interaction=broken))
    scenario, interaction, error = failing_command()

    with pytest.raises(RuntimeError):
        await errors_cog().on_app_command_error(interaction, error)

    assert len(answered(scenario)) == 1
    [embed] = log_channels.errors.embeds
    assert "RuntimeError: the record could not be built" in embed.description, (
        "the quiet trace alone keeps the failure out of Discord"
    )


async def test_an_error_without_an_original_is_recorded_under_its_own_type(
    deps, log_channels, analytics_events
):
    """A refused check reaches the handler as itself, with no `original`."""
    _, interaction, _ = failing_command()

    await errors_cog().on_app_command_error(
        interaction, app_commands.CheckFailure("only in a server")
    )

    [event] = failed_events(analytics_events)
    assert event["props"]["error_type"] == "CheckFailure"
    assert len(log_channels.errors.embeds) == 1
    assert len(await stored_failures(deps)) == 1


async def test_a_failed_command_is_counted_with_the_code_discord_answered(
    deps, log_channels
):
    from prometheus_client import REGISTRY

    labels = {"outcome": "failed", "code": "10062"}
    before = REGISTRY.get_sample_value("keiko_interactions_total", labels) or 0.0
    _, interaction, _ = failing_command()
    error = app_commands.CommandInvokeError(SimpleNamespace(name="links"), expired())

    await errors_cog().on_app_command_error(interaction, error)

    assert REGISTRY.get_sample_value("keiko_interactions_total", labels) == before + 1


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
    monkeypatch.setattr(Failing, "traces", [])
    interaction = interaction_in(
        guild, SimpleNamespace(qualified_name="moderations block links", _attr="block_links")
    )

    with pytest.raises(RuntimeError):
        await Failing.failing.callback(SimpleNamespace(), interaction)

    [trace] = Failing.traces
    assert trace.footnote == SECOND_RUN


async def test_a_feature_button_that_raises_keeps_its_footnote(deps, guild, second_run, monkeypatch):
    from app import forms as settings

    traces = []

    async def open_feature(interaction, command_key, source, **opening):
        traces.append(current_trace())
        raise RuntimeError("the feature broke")

    monkeypatch.setattr(callbacks, "open_feature", open_feature)

    with pytest.raises(RuntimeError):
        await settings.run_feature_command(interaction_in(guild), "block_links", "setup_dashboard")

    [trace] = traces
    assert trace.footnote == SECOND_RUN


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
async def checked_log_channel(monkeypatch):
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


async def _settle_loop():
    for _ in range(20):
        await asyncio.sleep(0)


async def _a_click_that_fails(deps, monkeypatch, guild=None, user=None):
    guild = guild or create_guild()
    user = user or create_member(guild, id=555, name="Tester")
    scenario = await FormScenario(
        guild=guild, user=user, locale="pt-br", mongo=deps.mongo_client
    ).start_command("default_roles")
    message = scenario.current_message
    button = scenario.component("continue")
    failed = []

    def fails_once(original):
        async def answer(self, **kwargs):
            if not failed:
                failed.append(True)
                raise discord.NotFound(
                    SimpleNamespace(status=404, reason="Not Found"),
                    {"code": 10062, "message": "Unknown interaction"},
                )
            return await original(self, **kwargs)

        return answer

    for name in ("edit_message", "defer"):
        monkeypatch.setattr(FakeResponse, name, fails_once(getattr(FakeResponse, name)))
    await scenario.click(button, on=message)
    await _settle_loop()
    return scenario, message, button


async def test_an_error_the_person_got_past_gets_a_check(deps, checked_log_channel, monkeypatch):
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
    scenario, message, button = await _a_click_that_fails(deps, monkeypatch)
    [error] = _render_errors(checked_log_channel)
    assert error.reactions == []

    await scenario.click(button, on=message)
    await _settle_loop()

    assert error.reactions == ["✅"], "the person clicked again and the setup went on"


async def test_an_error_nobody_got_past_keeps_no_check(deps, checked_log_channel, monkeypatch):
    await _a_click_that_fails(deps, monkeypatch)

    [error] = _render_errors(checked_log_channel)
    assert error.reactions == []


async def test_the_check_is_added_once(deps, checked_log_channel, monkeypatch):
    scenario, message, button = await _a_click_that_fails(deps, monkeypatch)
    await scenario.click(button, on=message)
    await _settle_loop()
    await scenario.select_option("Member", target="default_roles")
    await _settle_loop()

    [error] = _render_errors(checked_log_channel)
    assert error.reactions == ["✅"]


async def _open(deps, guild, user, command_key):
    await FormScenario(
        guild=guild, user=user, locale="pt-br", mongo=deps.mongo_client
    ).start_command(command_key)
    await _settle_loop()


async def test_an_error_is_checked_when_the_same_member_opens_the_feature_again(
    deps, checked_log_channel, monkeypatch
):
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    await _a_click_that_fails(deps, monkeypatch, guild, user)
    [error] = _render_errors(checked_log_channel)

    await _open(deps, guild, user, "default_roles")

    assert error.reactions == ["✅"], "a new command by the same person carried on"


async def test_an_error_is_not_checked_by_another_member_or_feature(
    deps, checked_log_channel, monkeypatch
):
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    await _a_click_that_fails(deps, monkeypatch, guild, user)
    [error] = _render_errors(checked_log_channel)

    await _open(deps, guild, create_member(guild, id=777, name="Other"), "default_roles")
    await _open(deps, guild, user, "block_links")

    assert error.reactions == []


async def test_a_pending_error_is_forgotten_after_the_window(
    deps, checked_log_channel, monkeypatch
):
    from app.constants import Commands

    monkeypatch.setattr(Commands, "ANALYTICS_RECOVERY_WINDOW_SECONDS", 0, raising=False)
    guild = create_guild()
    user = create_member(guild, id=555, name="Tester")
    await _a_click_that_fails(deps, monkeypatch, guild, user)
    [error] = _render_errors(checked_log_channel)

    await _open(deps, guild, user, "default_roles")

    assert error.reactions == []


async def test_the_errors_waiting_for_a_check_are_bounded(deps, checked_log_channel, monkeypatch):
    """Broke as: every form error waited the whole recovery window in a dict with no
    size, holding the message it posted, so an outage that failed every session
    for an hour kept every one of them (found by the memory soak, PR B). Shared
    behaviour: `DiscordLogsHandler`. Guaranteed: at most
    `Commands.ANALYTICS_RECOVERY_MAX_PENDING` errors wait; the oldest beyond it is
    forgotten as if its window had passed, and the newest still get their check.
    """
    from app.constants import Commands

    monkeypatch.setattr(Commands, "ANALYTICS_RECOVERY_MAX_PENDING", 2, raising=False)
    guild = create_guild()
    members = [create_member(guild, id=600 + index, name=f"M{index}") for index in range(3)]
    errors = []
    for member in members:
        await _a_click_that_fails(deps, monkeypatch, guild, member)
        errors.append(_render_errors(checked_log_channel)[-1])

    for member in members:
        await _open(deps, guild, member, "default_roles")

    assert [error.reactions for error in errors] == [[], ["✅"], ["✅"]]


class _NoopCoroutine:
    def __await__(self):
        yield
        return None

    def close(self):
        return None


class FakeChannel:
    def __init__(self, sends):
        self._sends = sends

    def send(self, **kwargs):
        self._sends.append(kwargs)
        return _NoopCoroutine()


@pytest.fixture
def bus(deps):
    """Both handlers, as `LoggerHooks.start` wires them."""
    from types import SimpleNamespace

    sends = []
    channel = FakeChannel(sends)
    bot = SimpleNamespace(
        loop=None,
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=lambda channel_id: channel,
    )

    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)

    discord_handler = logger_module.DiscordLogsHandler(bot)
    root.removeHandler(discord_handler)
    discord_handler.schedule_send = lambda coroutine: coroutine.close()
    stored = StoredLogsHandler()

    yield SimpleNamespace(discord=discord_handler, sends=sends)

    root.removeHandler(stored)
    root.setLevel(previous_level)


def record(name, message, level=logging.ERROR, **extra):
    made = logging.LogRecord(
        name=name, level=level, pathname="x.py", lineno=1,
        msg=message, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(made, key, value)
    return made


def stored_messages():
    wait(debug_logs.flush())

    return [document["message"] for document in fake_store().guild.logs.find({})]


def test_a_port_scanner_cannot_post_to_the_error_channel(bus):
    """The error channel is for Keiko's errors.

    `app/logger.py` attaches its handlers to the **root** logger, so every library
    in the process writes into them. `werkzeug` is set to ERROR, and the webhook API
    is a development server listening on `0.0.0.0`, so a port scanner sending TLS
    bytes to a plain HTTP port produced this, 984 times:

        91.231.89.186 - - [10/Sep/2026] code 400, message Bad request version ('\xc0#\xc0\xac')

    Each one became an embed in the admin error channel: 467 in a single day. The
    bot then rate-limited itself posting them — 1790 `429`s on the same channel over
    the same three days — which is a stranger on the internet degrading the bot by
    sending it garbage.

    Nothing is lost by keeping them out of Discord: every record still reaches
    `guild.logs` and the daily archive, which is where a 400 from a scanner belongs.
    """
    scanner = record(
        "werkzeug",
        "91.231.89.186 - - [10/Sep/2026] code 400, message Bad request version",
    )

    bus.discord.emit(scanner)

    assert bus.sends == [], (
        "a stranger sending garbage to the webhook port must not be able to "
        "write in the admin error channel, 467 times a day"
    )


def test_the_scanner_record_is_still_kept_where_it_belongs(bus):
    logging.getLogger("werkzeug").error("code 400, message Bad request version")

    assert any("Bad request version" in message for message in stored_messages()), (
        "it stays queryable in guild.logs and in the daily archive"
    )


def test_a_library_warning_about_itself_stays_out(bus):
    bus.discord.emit(record(
        "discord.gateway", "Shard ID None heartbeat blocked", level=logging.WARNING,
    ))

    assert bus.sends == []


def test_keikos_own_error_still_reaches_the_channel(bus):
    bus.discord.emit(record(
        "root", "Form error: NotFound: Unknown interaction",
        log_type=logconstants.COMMAND_ERROR_TYPE,
    ))

    assert len(bus.sends) == 1
    assert "Form error" in str(bus.sends[0]["embed"].description)


def test_a_module_of_ours_with_its_own_logger_still_reaches_the_channel(bus):
    """`app.core.trace` logs under its own name and is very much ours."""
    bus.discord.emit(record("app.core.trace", "twitch stream.online failed"))

    assert len(bus.sends) == 1


SECRET = "my bank password is hunter2"


def shown(embed):
    return "\n".join([embed.description or "", *(f"{f.name} {f.value}" for f in embed.fields)])


def a_message():
    guild = create_guild()
    return create_message(guild.text_channels[0], create_member(guild), SECRET)


@pytest.mark.regression
def test_an_error_about_a_message_names_its_length_never_its_text(log_channels):
    """An error never carries what a member wrote.

    Reported in the v1 review: `ErrorContext.from_message` copied the first 200 characters of
    the member's message into the error's context, and `with_error_context`, the listener
    context of then, did the same for every listener that received a message. The context becomes fields of the embed in the
    admin error channel, so a failure while checking a message published the member's text
    to whoever reads that channel.

    Shared behaviour: `ErrorContext` (`app/exceptions.py`), behind every error a feature logs
    about a message (block links, StreamElements), and the listener's error context.
    Exposed by: the review of what the error channel shows.

    The next review found the same in the prefix commands' error handler
    (`Errors.on_command_error`), whose warning quoted the whole message.

    Guaranteed: the error names the guild, the member, the channel and the length of the
    message, never its text; a prefix command's failure names the command and the length.
    """
    message = a_message()

    logger_module.error(
        "Failed to block link: RuntimeError: Discord is down",
        context=ErrorContext.from_message(flow="block_links", message=message),
    )

    posted = log_channels.errors.embeds
    assert len(posted) == 1
    assert "hunter2" not in shown(posted[0])
    assert str(len(SECRET)) in shown(posted[0]), "a length is fine"


@pytest.mark.regression
async def test_a_listener_that_fails_on_a_message_never_shows_its_text(
    log_channels, deps, monkeypatch
):
    from app.core.events import Events

    deps.bot.config.PREFIX = "ke!"
    monkeypatch.setattr(
        "app.commands.block_links.service.check_message",
        AsyncMock(side_effect=RuntimeError("Discord is down")),
    )

    await Events(deps.bot).on_message(a_message())

    shown_anywhere = "\n".join(shown(embed) for embed in log_channels.errors.embeds)
    assert log_channels.errors.embeds, "the failure still reaches the error channel"
    assert "hunter2" not in shown_anywhere
    assert str(len(SECRET)) in shown_anywhere, "a length is fine"


@pytest.mark.regression
async def test_a_prefix_command_that_fails_never_shows_its_message(deps, caplog):
    from discord.ext import commands

    from app.core.errors import Errors

    deps.bot.config.is_prod = lambda: False
    message = a_message()
    context = SimpleNamespace(guild=message.guild, message=message, invoked_with="mouse")

    with caplog.at_level("INFO"):
        await Errors(deps.bot).on_command_error(
            context, commands.BadArgument(f'Converting "{SECRET}" failed')
        )

    assert "hunter2" not in caplog.text
    assert "mouse" in caplog.text and str(len(SECRET)) in caplog.text


def direct_message(content):
    author = MockMember(id=111, name="TestUser")
    return MockMessage(
        id=1,
        content=content,
        author=author,
        channel=SimpleNamespace(id=55, guild=None, send=AsyncMock()),
    )


@pytest.mark.regression
@pytest.mark.parametrize("content", ["hi Keiko!", "ks!mouse", "https://spam.example"])
async def test_a_direct_message_reaches_no_admin_channel(
    content, log_channels, mock_cache
):
    """A direct message to Keiko is not an error.

    Broke as: `Events.on_message` read `message.guild.id` before asking where the
    message came from. A DM has no guild, so every DM to the bot raised an
    AttributeError inside `with_error_context`, and each one posted an error embed to
    the admin error channel.

    Shared behaviour: `Events.on_message`, the listener behind block links and the
    StreamElements commands. Consumer that exposed it: anyone writing to the bot.

    Guaranteed: nothing reads the guild of a DM: no error, no message in any admin
    channel, and no feature looks at it; a prefixed one gets only the slash hint.
    """
    from app.core import events as events_module

    await events_module.Events(SimpleNamespace()).on_message(direct_message(content))

    assert log_channels.errors.embeds == []
    assert log_channels.logs.embeds == [] and log_channels.calls.embeds == []
    assert [trace for trace in log_channels.traces if trace.is_noteworthy] == []
    assert mock_cache.call_count == 0, "no feature reads the settings of a DM"


def make_record(message, level=logging.ERROR, **extra):
    record = logging.LogRecord(
        name="keiko", level=level, pathname="app/services/thing.py",
        lineno=1, msg=message, args=(), exc_info=None,
    )
    record.asctime = "2026-08-23 10:00:00"
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.fixture
def handler():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    bot = SimpleNamespace(loop=MagicMock(), config=SimpleNamespace(
        ADMIN_LOGS_CHANNEL_ID=1, ADMIN_LOGS_ERROR_CHANNEL_ID=2,
        ADMIN_LOGS_COMMAND_CALL_ID=3, ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
    ), get_channel=lambda _id: None)

    instance = logger_module.DiscordLogsHandler(bot)
    logger_module.logger.removeHandler(instance)
    return instance


def test_a_huge_log_message_does_not_blow_past_the_embed_limit(handler):
    """Failures the production archive recorded, pinned so they cannot return.

    Two years of daily log files were indexed and grouped by signature. These are
    the ones whose cause still lived in the code. Each test names the count the
    archive holds, because a regression here is not hypothetical: it already
    happened that many times.

    Discord rejects the whole message, so the log about the failure is the
    thing that fails to be logged.
    """
    embed = handler.add_embed(make_record(
        "x" * 9000, log_type=logconstants.COMMAND_ERROR_TYPE
    ))

    assert len(embed.description) <= limits.EMBED_DESCRIPTION


def test_an_enormous_traceback_does_not_blow_past_the_embed_limit(handler):
    """`format_traceback_message` caps the frames at 3000 characters, but the
    exception text and the file path are appended afterwards and were not
    capped at all."""
    try:
        raise ValueError("y" * 6000)
    except ValueError:
        import sys
        record = make_record("boom")
        record.exc_info = sys.exc_info()
        embed = handler.add_embed(record)

    assert len(embed.description) <= limits.EMBED_DESCRIPTION


def test_the_clipped_description_keeps_the_beginning(handler):
    embed = handler.add_embed(make_record(
        "the part that matters " + "x" * 9000,
        log_type=logconstants.COMMAND_ERROR_TYPE,
    ))

    assert embed.description.startswith("the part that matters")


def test_a_config_saved_before_the_field_existed_does_not_raise():
    """Broke as: `config[ALLOWED_CHATS_KEY]["values"]` on a document written
    before that field existed, inside on_message, so it fired on every message
    that guild received.

    The block-links redesign fixed it by normalizing first. This pins the
    guarantee rather than the current call order, because the subscript is
    still there and only the normalizer keeps it safe.
    """
    legacy = {"block_links": {"enabled": True}}

    normalized = block_links.normalize_block_links_config(legacy)

    assert "values" in normalized["allowed_chats"]
    assert normalized["allowed_chats"]["values"] == []


def test_evaluating_a_message_against_a_legacy_config_does_not_raise():
    subject = block_links.MessageSubject(
        content="look at https://example.com",
        channel_id="100",
        author_role_ids=(),
    )

    evaluation = block_links.evaluate_message(subject, {"block_links": {"enabled": True}})

    assert evaluation is not None


NOTION = "https://api.notion.com/v1/pages"


def refused():
    body = b'{"object": "error", "message": "Notion is down"}'
    return respond(NOTION, body, status=500, content_type="application/json")


def a_gateway_page():
    body = b"<html><head><title>502 Bad Gateway</title></head></html>"
    return respond(NOTION, body, status=502, content_type="text/html")


def timed_out():
    raise requests.ReadTimeout("api.notion.com did not answer in 15 seconds")


@pytest.fixture
def report(deps, guild, monkeypatch):
    from app.commands.report.cog import Report

    reports = MagicMock()
    reports.send = AsyncMock()
    notion = SimpleNamespace(create_report=None)
    bot = SimpleNamespace(
        notion=notion,
        config=SimpleNamespace(ADMIN_REPORTS_CHANNEL_ID=1),
        get_channel=lambda channel_id: reports,
    )
    direct_messages = AsyncMock()
    monkeypatch.setattr(Report, "_send_report_dm", direct_messages)
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.command = SimpleNamespace(qualified_name="report")

    async def send(answer):
        notion.create_report = lambda **fields: answer()
        await Report.report.callback(
            Report(bot), interaction, "title", "description", "block_links"
        )

    return SimpleNamespace(
        send=send, reports=reports, interaction=interaction, direct_messages=direct_messages
    )


@pytest.mark.behavioral
@pytest.mark.parametrize(
    "answer",
    [timed_out, refused, a_gateway_page],
    ids=["timeout", "error-answer", "gateway-page"],
)
async def test_a_report_notion_does_not_take_still_arrives(report, answer, caplog):
    """A `/report` Notion does not take still reaches the team and the person who sent it.

    Reported in the v1 review, once every outside call got a time limit: Notion that does
    not answer in time now raises where it used to hang the bot, and `/report` raised with
    it, so the report was lost and the person saw an error. A Notion error answer was
    already lost the same way: the ticket number was read from a body that has none.

    Shared behaviour: `Report.report`, the one way a server reports a bug. Exposed by: the
    timeouts of the shared HTTP client.

    Guaranteed: when Notion fails or refuses, the report still goes to the reports channel
    under a Keiko ticket number, the person is answered and gets the copy in their DMs, and
    the failure is an error in the error channel: an exception named by its type only, a
    refusal by its status code and the start of its body, which may not be JSON at all (the
    gateway page of a 502 or 504 once made the error log itself raise and lose the report).
    """
    with caplog.at_level("INFO"):
        await report.send(answer)

    posted = report.reports.send.await_args.kwargs["embed"]
    assert "KEIKO-" in posted.title, "the report carries a ticket number of Keiko's"
    assert report.interaction.get_last_response()["type"] == "followup_send"
    report.direct_messages.assert_awaited_once()
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert errors and all("Notion" in record.getMessage() for record in errors)


@pytest.mark.behavioral
async def test_a_refusal_is_logged_by_its_status_and_the_start_of_its_body(report, caplog):
    with caplog.at_level("INFO"):
        await report.send(a_gateway_page)

    [error] = [record for record in caplog.records if record.levelname == "ERROR"]
    assert "502" in error.getMessage()
    assert "<html><head><title>502 Bad Gateway" in error.getMessage()


@pytest.fixture
def log_channel():
    """The admin log channel, plus the handler pair `LoggerHooks.start` installs."""
    sends = []

    channel = SimpleNamespace(send=lambda **kwargs: (sends.append(kwargs), _NoopCoroutine())[1])
    bot = SimpleNamespace(
        loop=MagicMock(),
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=lambda channel_id: channel,
        guilds=[object(), object(), object()],
        user=SimpleNamespace(id=99),
    )

    trace_service.clear_sinks()
    folding = logger_module.TraceFoldingHandler()
    handler = logger_module.DiscordLogsHandler(bot)
    logger_module.logger.removeHandler(handler)
    handler.schedule_send = lambda coroutine: coroutine.close()
    logger_module.logger.addHandler(folding)
    logger_module.logger.addHandler(handler)

    yield SimpleNamespace(bot=bot, sends=sends)

    logger_module.logger.removeHandler(folding)
    logger_module.logger.removeHandler(handler)
    trace_service.clear_sinks()


@pytest.fixture
def events(log_channel, monkeypatch):
    """The real cog, with only the Discord round trips stubbed out."""
    from app.core import events as events_module

    monkeypatch.setattr(
        setup_service,
        "GreetingsView",
        lambda *args, **kwargs: SimpleNamespace(send=lambda guild: _NoopCoroutine()),
    )
    return events_module.Events(log_channel.bot)


def guild_double(guild_id=4242, owner_id=313):
    return SimpleNamespace(
        id=guild_id,
        member_count=120,
        owner=None, owner_id=owner_id,
        preferred_locale="pt-BR",
    )


def rendered(embed):
    parts = [embed.title or "", embed.description or ""]
    parts += [f"{field.name} {field.value}" for field in embed.fields]
    return "\n".join(parts)


def embeds(log_channel):
    return [send["embed"] for send in log_channel.sends if "embed" in send]


async def test_a_guild_removing_keiko_reaches_the_log_channel(deps, events, log_channel):
    """A guild adding or removing Keiko has to reach the log channel.

    Reported: the "Left Guild" message stopped appearing. Nothing was broken in the
    listener — `guild.logs` in production holds every one of those records, and the
    matching `guild.removed` analytics events are all there. What was broken is the
    trip to Discord: every listener runs in a trace with `silent_when_clean=True`
    (`with_error_context` then, `trace.listener` now) so `on_message` cannot flood the channel,
    and `on_guild_remove` never fails. The line was folded into a trace that was
    then thrown away.

    The blast radius is every listener, but only these two lose anything today:
    `on_member_join`, `on_message` and `on_raw_message_edit` log nothing on a
    successful run, so their silence was always the intent. That is the difference
    this suite pins — the guild lifecycle publishes, the routine check does not.
    """
    guild = guild_double()
    deps.mongo_client.guild.settings.insert_one(
        {"_id": str(guild.id), "state": {"present": True}}
    )
    seed_cog(deps.mongo_client, "welcome_messages", {"guild_id": str(guild.id), "enabled": True})

    await events.on_guild_remove(guild)

    posted = embeds(log_channel)
    assert posted, "the record was written to guild.logs and never posted"
    left = [embed for embed in posted if embed.title == logconstants.EVENT_LEFT_GUILD_TITLE]
    assert len(left) == 1
    assert "Left guild by 313" in rendered(left[0])
    assert "4242" in rendered(left[0]), "the message has to say which guild left"


async def test_the_removal_analytics_event_was_never_the_broken_part(deps, events):
    """Production has the events and not the messages; keep both true together."""
    await events.on_guild_remove(guild_double())

    removed = await data().events_changes.find_by_guild("4242", types=["guild.removed"])
    assert len(removed) == 1


async def test_a_guild_adding_keiko_reaches_the_log_channel(deps, events, log_channel):
    """Production has no `guild.joined` document at all, so pin both halves.

    Nobody added Keiko in the days after analytics was deployed, which reads the
    same as a broken emit until something exercises the path.
    """
    await events.on_guild_join(guild_double(guild_id=777, owner_id=42))

    joined = [
        embed for embed in embeds(log_channel)
        if embed.title == logconstants.EVENT_JOIN_GUILD_TITLE
    ]
    assert len(joined) == 1
    assert "777" in rendered(joined[0])

    await analytics.written()
    joined = await data().events_changes.find_by_guild("777", types=["guild.joined"])
    assert len(joined) == 1


async def test_a_returning_guild_is_still_reported(deps, events, log_channel):
    deps.mongo_client.guild.settings.insert_one({"_id": "777", "state": {"present": False}})

    await events.on_guild_join(guild_double(guild_id=777))

    assert any(
        embed.title == logconstants.EVENT_JOIN_GUILD_TITLE
        for embed in embeds(log_channel)
    )


async def test_a_routine_listener_line_still_stays_out_of_the_channel(log_channel):
    """The flood protection is the reason the rule exists — it has to survive.

    `on_message` runs on every message in every guild; whatever it decides to
    log on a successful check belongs in the file, not in Discord.
    """
    async def routine():
        logger_module.info(
            "checked a message", log_type=logconstants.COMMAND_INFO_TYPE
        )

    async with trace_service.listener("on_message", 4242) as checked:
        await checked.run("block_links", routine())

    assert embeds(log_channel) == []


async def test_a_failing_listener_still_reports_as_a_failure(log_channel):
    async def broken():
        raise RuntimeError("Discord said no")

    async with trace_service.listener("on_member_join", 4242) as joined:
        await joined.run("welcome_messages", broken())

    posted = embeds(log_channel)
    assert posted, "an error was always published, and still is"
    assert any(
        logconstants.TRACE_RESULT_FAILURE in rendered(embed)
        or embed.title == logger_module.outcome_title(logconstants.TRACE_RESULT_FAILURE)
        for embed in posted
    )


def test_the_reported_event_vocabulary_is_the_one_the_channel_renders():
    """A type that publishes a trace must have a title and colour to publish with."""
    for log_type in logconstants.REPORTED_EVENT_TYPES:
        assert log_type in logconstants.LOG_TYPE_MAP


def test_a_silent_trace_with_no_event_line_is_not_noteworthy():
    trace = trace_service.Trace("on_message", silent_when_clean=True)
    trace.add("checked a message", logging.INFO, log_type=logconstants.COMMAND_INFO_TYPE)
    trace.finish()

    assert trace.is_noteworthy is False


GUILD_ID = "4242"


def left_line(log_channel):
    left = [
        embed for embed in embeds(log_channel)
        if embed.title == logconstants.EVENT_LEFT_GUILD_TITLE
    ]
    assert len(left) == 1
    return next(
        line for line in rendered(left[0]).splitlines() if "Active commands" in line
    )


@pytest.mark.regression
async def test_the_left_guild_line_names_what_was_on_by_its_saved_settings(
    deps, events, log_channel
):
    """The "Left Guild" message names the features that were on, by the record the bot runs on.

    Reported in the v1 review: `on_guild_remove` listed "Active commands" from the old
    moderations flags, which wave 1 stopped trusting (`is_feature_on` is the one source of
    "on"). A feature saved without the flag (StreamElements) was missing from the line, and a
    feature disabled while its flag stayed on was listed. The line is what whoever is on call
    reads to know what a server lost.

    Shared behaviour: `Events.on_guild_remove`, the listener every server removal runs.
    Exposed by: the wave 1 review of every reader of the old flags.

    Guaranteed: the line lists exactly the features `is_feature_on` calls on before Keiko
    pauses them, and says "none" when nothing was.
    """
    seed_cog(deps.mongo_client, "block_links", {"guild_id": GUILD_ID, "enabled": False})
    seed_cog(deps.mongo_client, "welcome_messages", {"guild_id": GUILD_ID, "enabled": True})
    seed_cog(deps.mongo_client, "stream_elements_commands", {"guild_id": GUILD_ID, "enabled": True})

    await events.on_guild_remove(guild_double(guild_id=int(GUILD_ID)))

    line = left_line(log_channel)
    assert "welcome_messages" in line
    assert "stream_elements_commands" in line
    assert "block_links" not in line, "paused, so not on"


@pytest.mark.regression
async def test_a_guild_with_nothing_on_says_none(deps, events, log_channel):
    seed_cog(deps.mongo_client, "block_links", {"guild_id": GUILD_ID, "enabled": False})

    await events.on_guild_remove(guild_double(guild_id=int(GUILD_ID)))

    assert "Active commands: none" in left_line(log_channel)


async def test_the_join_names_an_owner_discord_py_never_cached(deps, events, log_channel):
    """Broke as: the join read `guild.owner`, the owner's cached member, which is
    None once no server's members are downloaded at start, so the join raised
    before it recorded or posted anything. Shared behaviour: `Events` join and
    leave. Guaranteed: both read `guild.owner_id`, so the owner is named and
    recorded whether discord.py holds their member or not."""
    await events.on_guild_join(guild_double(guild_id=778, owner_id=42))
    await events.on_guild_remove(guild_double(guild_id=778, owner_id=42))

    posted = {embed.title: rendered(embed) for embed in embeds(log_channel)}
    assert "<@42>" in posted[logconstants.EVENT_JOIN_GUILD_TITLE]
    assert "Left guild by 42" in posted[logconstants.EVENT_LEFT_GUILD_TITLE]
    stored = deps.mongo_client.guild.settings.find_one({"_id": "778"})
    assert stored["state"]["owner_id"] == "42"


@pytest.fixture
def traces():
    """Every trace that closed, with `logger.*` folded into its timeline."""
    trace_service.clear_sinks()
    captured = []
    trace_service.register_sink(captured.append)

    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)

    yield captured

    logger_module.logger.removeHandler(folding)
    trace_service.clear_sinks()


@pytest.fixture
def twitch_client(monkeypatch):
    """The real blueprint, with the bot and the deferred work stubbed out."""
    scheduled = []

    def capture(coroutine, name, **kwargs):
        coroutine.close()
        scheduled.append(name)

    monkeypatch.setattr(webhooks_package, "schedule_webhook_job", capture, raising=False)
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(
        twitch=SimpleNamespace(
            verify_twitch_signature=lambda request: True,
            check_request_is_a_challenge=lambda request: False,
        ),
        loop=None,
    ), raising=False)

    flask_app = Flask(__name__)
    flask_app.register_blueprint(webhooks)

    return SimpleNamespace(post=flask_app.test_client().post, scheduled=scheduled)


def timeline(trace):
    return " | ".join(line["message"] for line in trace.lines)


def post_event(client, event_type, streamer="Gaules"):
    return client.post("/twitch", json={
        "subscription": {"type": event_type},
        "event": {"broadcaster_user_name": streamer},
    })


def test_a_stream_going_live_names_the_streamer_in_its_log(twitch_client, traces):
    """A webhook that defers its work still has to say what arrived.

    Reported: the admin log showed which streamer went offline, but a stream that
    went live produced a message with an empty timeline. The offline branch logged
    one line synchronously inside the request; the online branch logged nothing and
    scheduled a coroutine, and everything that coroutine logged was appended to a
    trace the teardown had already closed and posted.

    Reported later, from production: that fix posted two messages for one Twitch
    live, a `🔔 Webhook Event` naming the streamer and a `⏰ Scheduled Job` with the
    fan-out, because the request and the job each closed a trace of their own.

    Three things must stay guaranteed:

    - every webhook branch names its subject inside the request, so the message
      that arrives is readable even if the deferred work never finishes;
    - deferred work never writes into a trace that was already posted;
    - the first job a request schedules continues the request's message, so one
      event is one message, and a request that cannot schedule still posts its own.
    """
    response = post_event(twitch_client, "stream.online")

    assert response.status_code == 200
    assert len(traces) == 1
    assert "gaules" in timeline(traces[0]).lower(), (
        "the message that arrives must say who went live"
    )


def test_a_stream_going_offline_keeps_naming_the_streamer(twitch_client, traces):
    post_event(twitch_client, "stream.offline")

    assert "gaules" in timeline(traces[0]).lower()


def test_both_branches_hand_their_work_to_the_same_scheduler(twitch_client, traces):
    post_event(twitch_client, "stream.online")
    post_event(twitch_client, "stream.offline")

    assert len(twitch_client.scheduled) == 2, (
        "webhooks run on the Flask thread, where create_task is not safe"
    )


async def test_deferred_work_writes_into_its_own_message_not_the_closed_one(traces):
    """The request is posted and gone before the fan-out logs anything."""
    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")

    with trace_service.trace_scope("twitch", source="webhook"):
        logger_module.info("stream.online — gaules")
        deferred = trace_service.run_traced(fan_out(), "twitch stream.online", source="job")

    assert len(traces) == 1, "the request closes when Flask returns"

    await deferred

    assert len(traces) == 2, "the deferred work is its own unit of work"
    assert "Notifications sent" in timeline(traces[1])
    assert "Notifications sent" not in timeline(traces[0]), (
        "a line added to a trace that was already posted is a line nobody reads"
    )


async def test_the_scheduler_puts_the_job_on_the_bot_loop_with_a_trace_of_its_own(
    traces, monkeypatch,
):
    """The one line the original bug lived on, exercised end to end.

    Every other test here stubs the scheduler out, so losing `source="job"`,
    swapping the arguments or going back to `create_task` would pass them all.
    """
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=loop), raising=False)

    ran = asyncio.Event()

    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")
        ran.set()

    schedule_webhook_job(fan_out(), "twitch stream.online — gaules")

    await asyncio.wait_for(ran.wait(), timeout=1)
    for _ in range(100):
        if traces:
            break
        await asyncio.sleep(0.01)

    assert len(traces) == 1
    assert traces[0].source == "job", "a webhook message is not a job message"
    assert traces[0].name == "twitch stream.online — gaules"
    assert "Notifications sent" in timeline(traces[0])


async def test_deferred_work_that_explodes_still_closes_its_message(traces):
    async def broken():
        raise RuntimeError("twitch API is down")

    await trace_service.run_traced(broken(), "twitch stream.online", source="job")

    assert len(traces) == 1
    assert traces[0].has_error


async def _settle_traces(traces, count):
    for _ in range(100):
        if len(traces) >= count:
            return
        await asyncio.sleep(0.01)


def posted(traces):
    return [trace for trace in traces if trace.is_noteworthy]


async def test_a_webhook_that_defers_its_work_posts_one_message_named_after_the_event(
    traces, monkeypatch,
):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=loop), raising=False)
    ran = asyncio.Event()

    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")
        ran.set()

    with trace_service.trace_scope("twitch", source="webhook") as request:
        logger_module.info("stream.online — gaules")
        schedule_webhook_job(fan_out(), "twitch stream.online — gaules")

    await asyncio.wait_for(ran.wait(), timeout=1)
    await _settle_traces(traces, 2)

    messages = posted(traces)
    assert len(messages) == 1, "one Twitch live is one message in the log channel"
    event = messages[0]
    assert event.source == "webhook" and event.name == "twitch"
    assert "gaules" in timeline(event), "the message still says who went live"
    assert "Notifications sent" in timeline(event), "and what was done about it"
    assert event.started_at == request.started_at, (
        "the duration covers the whole event, from arrival to the last guild"
    )


def test_a_hand_over_that_cannot_be_scheduled_leaves_the_request_message_intact(
    traces, monkeypatch,
):
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=None), raising=False)

    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")

    with pytest.raises(Exception):
        with trace_service.trace_scope("twitch", source="webhook"):
            logger_module.info("stream.online — gaules")
            schedule_webhook_job(fan_out(), "twitch stream.online — gaules")

    messages = posted(traces)
    assert len(messages) == 1, "the request is the only message that can arrive"
    assert "gaules" in timeline(messages[0])


async def test_a_second_job_in_the_same_request_gets_a_message_of_its_own(
    traces, monkeypatch,
):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=loop), raising=False)
    done = []

    async def reminder(number):
        logger_module.info(f"birthday reminder {number} processed")
        done.append(number)

    with trace_service.trace_scope("reminder", source="webhook"):
        logger_module.info("2 reminder(s) notified")
        schedule_webhook_job(reminder(1), "birthday reminder 1")
        schedule_webhook_job(reminder(2), "birthday reminder 2")

    for _ in range(100):
        if len(done) == 2:
            break
        await asyncio.sleep(0.01)
    await _settle_traces(traces, 3)

    messages = posted(traces)
    assert len(messages) == 2
    request = next(trace for trace in messages if trace.source == "webhook")
    second = next(trace for trace in messages if trace.source == "job")
    assert "reminder 1 processed" in timeline(request)
    assert "reminder 2 processed" not in timeline(request)
    assert second.name == "birthday reminder 2"


async def test_an_error_before_the_hand_over_still_colours_the_message(
    traces, monkeypatch,
):
    """A failure logged in the request survives into the message that reports it.

    What breaks without it: the hand-over copied the request's lines into the
    successor but not the level they were logged at, so a request that had
    already reported an error handed over a red line under a trace that
    believed nothing had gone wrong. The message arrived blue, and the result
    line said the work succeeded while its own timeline showed the failure.
    """
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(loop=loop), raising=False)
    ran = asyncio.Event()

    async def fan_out():
        logger_module.info("Notifications sent for gaules in 3 guilds")
        ran.set()

    with trace_service.trace_scope("twitch", source="webhook"):
        logger_module.error("Guild 123 refused the notification")
        schedule_webhook_job(fan_out(), "twitch stream.online — gaules")

    await asyncio.wait_for(ran.wait(), timeout=1)
    await _settle_traces(traces, 2)

    event = posted(traces)[0]
    assert "refused" in timeline(event), "the failure is on the message"
    assert event.has_error, "and the message knows it is a failure"
    assert event.result == "failure"


@pytest.fixture
async def bot_loop(monkeypatch):
    monkeypatch.setattr(
        app_module, "bot", SimpleNamespace(loop=asyncio.get_running_loop()), raising=False
    )


@pytest.mark.regression
async def test_a_line_logged_after_the_hand_over_reaches_the_jobs_message(traces, bot_loop):
    """What a webhook logs after handing its trace to a job reaches the job's message.

    Reported in the wave 1 review: `schedule_webhook_job` hands the request's trace to its
    first job, so one event is one message, and marks the request superseded. Everything the
    request logged afterwards (the next reminder of the same callback, the refusal or the
    failure its status hook writes) was added to the superseded request, which is never
    posted: those lines reached `guild.logs` and no message. And a job that finished before
    the request closed was posted before those lines could arrive.

    Shared behaviour: `Trace.handover` (`app/core/trace.py`), behind every webhook that
    defers its work (Twitch, YouTube, the reminders). Exposed by: a reminder callback whose
    second reminder vanished from the log channel.

    Guaranteed: once a request has handed its trace over, every line it logs lands on the
    job's message, and that message is posted once both the request and the job are done,
    whichever ends first.
    """
    async def birthday():
        logger_module.info("birthday reminder 4001 processed")

    with trace_service.trace_scope("reminder", source="webhook"):
        logger_module.info("2 reminder(s) notified")
        schedule_webhook_job(birthday(), "birthday reminder 4001")
        logger_module.error("reminder 7001 (youtube_notification) failed: Timeout")

    await _settle_traces(traces, 2)

    messages = posted(traces)
    assert len(messages) == 1
    assert "reminder 7001" in timeline(messages[0]), "the line after the hand-over arrives"
    assert messages[0].has_error, "and it colours the message it lands on"


@pytest.mark.regression
async def test_the_jobs_message_waits_for_the_request_that_handed_it_over(traces, bot_loop):
    async def quick():
        logger_module.info("Notifications sent for gaules in 0 guilds")

    with trace_service.trace_scope("twitch", source="webhook"):
        logger_module.info("stream.online — gaules")
        schedule_webhook_job(quick(), "twitch stream.online — gaules")
        await asyncio.sleep(0.05)
        logger_module.warn("/v1/webhooks/twitch answered slowly")

    await _settle_traces(traces, 2)

    messages = [trace for trace in traces if not trace.superseded]
    assert len(messages) == 1, "one event, one message"
    assert "answered slowly" in timeline(messages[0]), (
        "the job ended first, and its message still waited for the request's last line"
    )


def embeds_posted(log_channels):
    return (
        log_channels.logs.embeds
        + log_channels.errors.embeds
        + log_channels.calls.embeds
        + log_channels.actions.embeds
    )


def embed_timeline(embed):
    return {field.name: field.value for field in embed.fields}["Timeline"]


@pytest.fixture
def stored_documents():
    """What reached `guild.logs`, read after the request."""
    handler = logger_module.StoredLogsHandler()
    yield debug_logs.drain
    logging.getLogger().removeHandler(handler)


@pytest.fixture
def stored_lines(stored_documents):
    """The messages of what reached `guild.logs`."""
    return lambda: [document["message"] for document in stored_documents()]


@pytest.fixture
def client(monkeypatch):
    """The real blueprint on an app named like production's, so Flask's own error
    log is Keiko's; Twitch signs whatever `signed` says."""
    state = SimpleNamespace(signed=True, scheduled=[])

    def capture(coroutine, name, **kwargs):
        coroutine.close()
        state.scheduled.append(name)

    monkeypatch.setattr(webhooks_package, "schedule_webhook_job", capture)
    monkeypatch.setattr(app_module, "bot", SimpleNamespace(
        twitch=SimpleNamespace(
            verify_twitch_signature=lambda request: state.signed,
            check_request_is_a_challenge=lambda request: False,
        ),
        loop=None,
    ), raising=False)
    flask_app = Flask("app.integrations.http")
    flask_app.register_blueprint(webhooks_package.webhooks)
    test_client = flask_app.test_client()
    state.app = flask_app
    state.get = test_client.get
    state.post = test_client.post
    return state


def stream_online(client):
    return client.post("/twitch", json={
        "subscription": {"type": "stream.online"},
        "event": {"broadcaster_user_name": "Gaules"},
    })


def refusals(route, status):
    labels = {"route": route, "status": str(status)}
    return REGISTRY.get_sample_value("keiko_webhook_refusals_total", labels) or 0.0


@pytest.mark.regression
def test_the_healthcheck_opens_no_trace(client, log_channels):
    """A webhook request posts to Discord only when it failed, and a refused one never.

    Broke as: every request under /v1/webhooks opened a trace that was posted to the
    Discord logs channel, the `/healthcheck` an uptime monitor calls every few
    seconds included, so the channel filled with messages saying nothing happened.
    A refused request was worse: a forged Twitch notice was logged as an error,
    which posted to the error channel, and its trace posted to the logs channel, so
    a stranger could flood both, bury the real errors and push the bot towards
    Discord's limit of invalid requests.

    Shared behaviour: `webhook_trace`, `open_webhook_trace` and
    `settle_by_status` (`app/integrations/http.py`), `Trace.is_noteworthy`,
    `Trace.publishing`, `DiscordLogsHandler.send_trace`, `schedule_webhook_job`.
    Consumers: the Twitch, YouTube and reminder webhooks and the jobs they schedule.

    Found in the second review: a route that answers a 5xx without raising
    logged nothing at error, so its silent trace never posted.

    Found on the tree merged with the webhooks branch: YouTube's route answers 503
    on purpose while the Data API does not show a new video yet, so the hub
    delivers the notice again later; the hook took that for a failure and posted
    `answered 503` on every such delivery.

    Guaranteed: the healthcheck opens no trace; a request, and the jobs it hands its
    work to, post only when they failed, and a warning stays in `guild.logs`; a
    route that raises, or answers a 5xx other than 503, still posts its error and
    its trace; a 503 without an error asks the sender to try again later, so it
    posts nothing and stores no error; a request answered with a 4xx posts nothing,
    is counted by route and status, and keeps its lines in `guild.logs`.
    """
    response = client.get("/healthcheck")

    assert response.status_code == 200
    assert log_channels.traces == []
    assert embeds_posted(log_channels) == []


@pytest.mark.regression
def test_a_webhook_that_did_its_job_posts_nothing(client, log_channels):
    response = stream_online(client)

    assert response.status_code == 200
    assert len(log_channels.traces) == 1, "the request is still one unit of work"
    assert embeds_posted(log_channels) == []


@pytest.mark.regression
def test_its_lines_still_reach_the_stored_logs(client, log_channels, stored_lines):
    stream_online(client)

    assert any("gaules" in line for line in stored_lines())


@pytest.mark.regression
def test_a_webhook_that_only_warned_stays_out_of_discord(log_channels, stored_lines):
    with webhooks_package.webhook_trace("reminder"):
        logger_module.warn("Unknown reminder title: something")

    assert embeds_posted(log_channels) == []
    assert any("Unknown reminder title" in line for line in stored_lines())


@pytest.mark.regression
def test_a_webhook_that_failed_still_posts_its_error_and_its_trace(log_channels):
    with webhooks_package.webhook_trace("reminder"):
        logger_module.error("reminders-api refused the payload")

    assert len(log_channels.errors.embeds) == 1, "the error keeps a message of its own"
    [message] = log_channels.logs.embeds
    assert "reminders-api refused the payload" in embed_timeline(message)


@pytest.mark.regression
def test_a_route_that_raises_still_posts_its_error_and_its_trace(
    client, log_channels, monkeypatch
):
    def broken(request):
        raise RuntimeError("the Twitch secret is not configured")

    monkeypatch.setattr(app_module.bot.twitch, "verify_twitch_signature", broken)

    response = stream_online(client)

    assert response.status_code == 500
    [error] = log_channels.errors.embeds
    assert "the Twitch secret is not configured" in error.description
    [message] = log_channels.logs.embeds
    assert "RuntimeError" in embed_timeline(message)


@pytest.mark.regression
@pytest.mark.parametrize("status", [500, 502, 504])
def test_a_route_that_answers_a_server_error_without_raising_still_posts(
    client, log_channels, status
):
    client.app.view_functions["webhooks.twitch_webhook"] = lambda: ("Twitch is down", status)

    response = stream_online(client)

    assert response.status_code == status
    [error] = log_channels.errors.embeds
    assert f"/twitch answered {status}" in error.description
    [message] = log_channels.logs.embeds
    assert f"answered {status}" in embed_timeline(message)


@pytest.mark.regression
def test_a_route_that_asks_to_be_called_again_later_posts_nothing(
    client, log_channels, stored_documents
):
    """A 503 is how a route asks the sender to deliver again later, as YouTube's
    does while the Data API does not show a new video yet."""

    def not_yet():
        logger_module.info("video is not visible yet. The hub will deliver it again.")
        return "Video not visible yet", 503

    client.app.view_functions["webhooks.twitch_webhook"] = not_yet

    response = stream_online(client)

    assert response.status_code == 503
    assert embeds_posted(log_channels) == [], "a delivery to try again later is not a failure"
    documents = stored_documents()
    assert [d["message"] for d in documents if d["level"] == "ERROR"] == []
    assert any("not visible yet" in d["message"] for d in documents)


@pytest.mark.regression
def test_a_forged_request_posts_nothing_and_is_counted(
    client, log_channels, stored_lines
):
    client.signed = False
    before = refusals("/twitch", 403)

    response = stream_online(client)

    assert response.status_code == 403
    assert embeds_posted(log_channels) == [], "a refusal is not a failure"
    assert refusals("/twitch", 403) == before + 1
    lines = stored_lines()
    assert any("Invalid Twitch signature" in line for line in lines)
    assert any("refused with 403" in line for line in lines)


@pytest.mark.regression
def test_a_request_refused_by_flask_itself_is_counted_by_its_status(
    client, log_channels
):
    """The status is read from the response, whoever wrote it: here Flask, for a
    body that is not JSON, while the route reads it."""
    before = refusals("/twitch", 415)

    response = client.post("/twitch", data="not json", content_type="text/plain")

    assert response.status_code == 415
    assert embeds_posted(log_channels) == []
    assert refusals("/twitch", 415) == before + 1


async def _settle_channels(log_channels, done, count):
    for _ in range(100):
        if len(done) == count and len(log_channels.traces) == count + 1:
            return
        await asyncio.sleep(0.01)


@pytest.mark.regression
async def test_the_jobs_of_a_request_stay_quiet_unless_they_fail(
    log_channels, monkeypatch
):
    monkeypatch.setattr(
        app_module, "bot", SimpleNamespace(loop=asyncio.get_running_loop()),
        raising=False,
    )
    done = []

    async def birthday(number):
        if number == 2:
            logger_module.warn(f"birthday reminder {number} found no channel")
        logger_module.info(f"birthday reminder {number} processed")
        done.append(number)

    with webhooks_package.webhook_trace("reminder"):
        schedule_webhook_job(birthday(1), "birthday reminder 1")
        schedule_webhook_job(birthday(2), "birthday reminder 2")
    await _settle_channels(log_channels, done, 2)

    assert done == [1, 2]
    assert embeds_posted(log_channels) == [], "two birthdays and a warning are not messages"


@pytest.mark.regression
async def test_a_job_that_fails_still_posts_its_own_message(log_channels, monkeypatch):
    monkeypatch.setattr(
        app_module, "bot", SimpleNamespace(loop=asyncio.get_running_loop()),
        raising=False,
    )
    done = []

    async def birthday(number):
        if number == 2:
            logger_module.error(f"birthday reminder {number} could not be sent")
        done.append(number)

    with webhooks_package.webhook_trace("reminder"):
        schedule_webhook_job(birthday(1), "birthday reminder 1")
        schedule_webhook_job(birthday(2), "birthday reminder 2")
    await _settle_channels(log_channels, done, 2)

    assert len(log_channels.errors.embeds) == 1
    [message] = log_channels.logs.embeds
    assert "could not be sent" in embed_timeline(message)

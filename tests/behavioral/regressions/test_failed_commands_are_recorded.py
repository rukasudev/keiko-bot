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

Guaranteed: a failed command puts a message with its traceback in the error
channel and a `command.failed` event carrying the error type before the person
is answered, and a record in `audit.errors` when Mongo takes it within
`COMMAND_FAILURE_STORE_SECONDS`; the person is answered even when the answer
fails, while the record is still being written, and when recording itself
raises, which reaches the error channel too; a Mongo stall holds no thread of
the default executor; the handler adds one message, the error. (A failed
`keiko_command` also posts its own trace in the calls channel: that message is
the command's, not the handler's.)
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from app.cogs.errors import Errors
from app.constants import Commands
from tests.behavioral.harness.fake_interaction import FakeInteraction, FakeResponse
from tests.behavioral.harness.message_store import MessageStore
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def errors_cog():
    return Errors(SimpleNamespace(config=SimpleNamespace(is_prod=lambda: False)))


def failing_command():
    """A command interaction and the error discord.py hands to the tree's handler."""
    guild = create_guild()
    member = create_member(guild, id=777, name="Rukasu")
    interaction = FakeInteraction(
        MessageStore(), guild=guild, user=member, locale=discord.Locale.american_english
    )
    interaction.command = SimpleNamespace(
        qualified_name="moderations block links", _attr="block_links"
    )
    try:
        raise KeyError("allowed_links")
    except KeyError as original:
        error = app_commands.CommandInvokeError(SimpleNamespace(name="links"), original)
    return interaction, error


def expired():
    return discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"),
        {"code": 10062, "message": "Unknown interaction"},
    )


def stored_failures(deps):
    return list(deps.mongo_client.audit.errors.find({}))


def failed_events(analytics_events):
    return [event for event in analytics_events if event["event"] == "command.failed"]


def answered(interaction):
    return [
        message
        for message in interaction.store.messages.values()
        if message.embeds and message.embeds[0].title.startswith("🚨")
    ]


def posted(log_channels):
    return (
        log_channels.logs.embeds
        + log_channels.errors.embeds
        + log_channels.calls.embeds
        + log_channels.actions.embeds
    )


class StalledMongo:
    """A Mongo that takes the connection and never answers a write."""

    def __init__(self, released):
        self.released = released

    def __getattr__(self, name):
        return self

    def insert_one(self, document):
        self.released.wait(10)


class StalledMotor:
    """The same Mongo seen through motor: the write is awaited forever."""

    def __getattr__(self, name):
        return self

    async def insert_one(self, document):
        await asyncio.Event().wait()


@pytest.fixture
def stalled_mongo(monkeypatch):
    """Mongo stops answering writes, for the sync and the async client alike."""
    released = threading.Event()
    monkeypatch.setattr("app.data.cogs.mongo_client", StalledMongo(released))
    monkeypatch.setattr("app.data.cogs.motor_client", StalledMotor())
    monkeypatch.setattr(Commands, "COMMAND_FAILURE_STORE_SECONDS", 0.05, raising=False)
    yield
    released.set()


async def test_a_failed_command_reaches_the_error_channel_the_audit_and_analytics(
    deps, log_channels, analytics_events
):
    interaction, error = failing_command()

    await errors_cog().on_app_command_error(interaction, error)

    [embed] = log_channels.errors.embeds
    assert "moderations block links" in embed.description
    assert "KeyError" in embed.description
    assert 'raise KeyError("allowed_links")' in embed.description, (
        "the traceback is what the error channel is read for"
    )
    [stored] = stored_failures(deps)
    assert stored["command_key"] == "block_links"
    assert "KeyError" in stored["error_message"]
    [event] = failed_events(analytics_events)
    assert event["props"]["error_type"] == "KeyError"
    assert event["props"]["command"] == "moderations block links"
    assert event["feature"] == "block_links"
    [answer] = answered(interaction)
    assert answer.ephemeral


async def test_a_failure_is_recorded_even_when_the_answer_cannot_be_sent(
    deps, log_channels, analytics_events, monkeypatch
):
    async def refused(self, *args, **kwargs):
        raise expired()

    monkeypatch.setattr(FakeResponse, "send_message", refused)
    interaction, error = failing_command()

    await errors_cog().on_app_command_error(interaction, error)

    assert len(log_channels.errors.embeds) == 1
    assert len(stored_failures(deps)) == 1
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
    interaction, error = failing_command()

    await errors_cog().on_app_command_error(interaction, error)

    assert reported_when_answered == {"error_channel": 1, "analytics": 1}
    assert len(stored_failures(deps)) == 1, "and the record follows"


async def test_the_person_is_answered_while_the_record_is_still_being_written(
    deps, log_channels, stalled_mongo, monkeypatch
):
    """Discord gives three seconds, and a failure late in them has none to spare."""
    monkeypatch.setattr(Commands, "COMMAND_FAILURE_STORE_SECONDS", 1.0, raising=False)
    interaction, error = failing_command()

    handling = asyncio.ensure_future(errors_cog().on_app_command_error(interaction, error))
    await asyncio.sleep(0.2)
    answered_while_writing = len(answered(interaction))
    await asyncio.wait_for(handling, timeout=3)

    assert answered_while_writing == 1, "the answer does not wait for the record"


async def test_a_store_that_never_returns_still_lets_the_answer_arrive(
    deps, log_channels, analytics_events, stalled_mongo
):
    interaction, error = failing_command()

    await asyncio.wait_for(errors_cog().on_app_command_error(interaction, error), timeout=2)

    assert len(answered(interaction)) == 1
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
              for interaction, error in failures)
        ),
        timeout=5,
    )
    unrelated = asyncio.ensure_future(asyncio.to_thread(time.monotonic))
    done, _ = await asyncio.wait({unrelated}, timeout=1)

    assert done, "a thread call unrelated to the failures waited behind the stalled writes"
    assert all(len(answered(interaction)) == 1 for interaction, _ in failures)


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
    interaction, error = failing_command()

    await errors_cog().on_app_command_error(interaction, error)

    assert [embed.title for embed in posted(log_channels)] == ["❌ Command Error"]
    assert len(log_channels.errors.embeds) == 1, "and it is in the error channel"


async def test_a_failure_while_recording_still_answers_and_reaches_the_error_channel(
    deps, log_channels, monkeypatch
):
    def broken(**kwargs):
        raise RuntimeError("the record could not be built")

    monkeypatch.setattr(
        "app.cogs.errors.ErrorContext", SimpleNamespace(from_interaction=broken)
    )
    interaction, error = failing_command()

    with pytest.raises(RuntimeError):
        await errors_cog().on_app_command_error(interaction, error)

    assert len(answered(interaction)) == 1
    [embed] = log_channels.errors.embeds
    assert "RuntimeError: the record could not be built" in embed.description, (
        "the quiet trace alone keeps the failure out of Discord"
    )


async def test_an_error_without_an_original_is_recorded_under_its_own_type(
    deps, log_channels, analytics_events
):
    """A refused check reaches the handler as itself, with no `original`."""
    interaction, _ = failing_command()

    await errors_cog().on_app_command_error(
        interaction, app_commands.CheckFailure("only in a server")
    )

    [event] = failed_events(analytics_events)
    assert event["props"]["error_type"] == "CheckFailure"
    assert len(log_channels.errors.embeds) == 1
    assert len(stored_failures(deps)) == 1


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
import logging

import pytest

from app import logger as logger_module
from app.constants import LogTypes as logconstants
from app.logger import StoredLogsHandler
from app.services import debug_logs
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app.exceptions import ErrorContext
from tests.mocks.discord import (
    create_guild,
    create_member,
    create_message,
    MockMember,
    MockMessage,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("logging")]


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
    debug_logs.flush()
    from app.data import logs as logs_data

    return [document["message"] for document in logs_data.mongo_client.guild.logs.find({})]


def test_a_port_scanner_cannot_post_to_the_error_channel(bus):
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
    """`app.services.trace` logs under its own name and is very much ours."""
    bus.discord.emit(record("app.services.trace", "twitch stream.online failed"))

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
    from app.cogs.events import Events

    deps.bot.config.PREFIX = "ke!"
    monkeypatch.setattr(
        "app.services.block_links.check_message",
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

    from app.cogs.errors import Errors

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
        id=1, content=content, author=author, channel=SimpleNamespace(id=55, guild=None)
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

    Guaranteed: a DM is ignored before anything reads its guild: no error, no message
    in any admin channel, and no feature looks at it.
    """
    from app.cogs import events as events_module

    await events_module.Events(SimpleNamespace()).on_message(direct_message(content))

    assert log_channels.errors.embeds == []
    assert log_channels.logs.embeds == [] and log_channels.calls.embeds == []
    assert [trace for trace in log_channels.traces if trace.is_noteworthy] == []
    assert mock_cache.call_count == 0, "no feature reads the settings of a DM"

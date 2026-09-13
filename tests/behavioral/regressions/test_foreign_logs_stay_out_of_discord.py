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

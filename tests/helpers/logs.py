"""The admin log channels as the real handler pair fills them, detached from the
global logger: one fake channel per admin channel, recording what each was sent."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import logger as logger_module
from app.core import journey
from app.core import trace as trace_service


class _NoopCoroutine:
    def __await__(self):
        yield
        return None

    def close(self):
        return None


class FakeChannel:
    """Records what would have been posted, without a coroutine ever running."""

    def __init__(self, name, sends, routed):
        self.name = name
        self._sends = sends
        self._routed = routed

    def send(self, **kwargs):
        self._sends.append(kwargs)
        self._routed.append(self.name)
        return _NoopCoroutine()


@pytest.fixture
def discord_logs():
    """A handler wired to fake channels, detached from the global logger."""
    sends = []
    routed = []
    ids = {1: "logs", 2: "errors", 3: "calls", 4: "actions"}
    channels = {name: FakeChannel(name, sends, routed) for name in ids.values()}

    bot = SimpleNamespace(
        loop=MagicMock(),
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=lambda channel_id: channels.get(ids.get(channel_id)),
    )

    trace_service.clear_sinks()
    # Folding lives on the bus now, not inside DiscordLogsHandler, so the wiring
    # under test is the same pair `LoggerHooks.start` installs.
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    handler = logger_module.DiscordLogsHandler(bot)
    logger_module.logger.removeHandler(handler)
    handler.schedule_send = lambda coroutine: coroutine.close()

    def dispatch(record):
        """What `Logger.callHandlers` does: every handler sees every record.

        Folding and rendering are two handlers now, so a test that drives only
        the Discord one is testing half the bus.
        """
        folding.emit(record)
        handler.emit(record)

    yield SimpleNamespace(
        handler=handler, channels=channels, sends=sends, routed=routed,
        emit=dispatch,
    )

    logger_module.logger.removeHandler(folding)

    trace_service.clear_sinks()


@pytest.fixture
def closed_traces():
    """Every trace that closes, as the folding handler hands it to its sinks."""
    trace_service.clear_sinks()
    captured: list = []
    trace_service.register_sink(captured.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    yield captured
    logger_module.logger.removeHandler(folding)
    trace_service.clear_sinks()


@pytest.fixture
def journeys():
    """Every journey the logger would publish, by session id, as published."""
    journey.clear()
    seen: dict = {}
    journey.set_publisher(lambda story: seen.__setitem__(story.session_id, story))
    journey.install()
    yield seen
    journey.clear()
    journey.set_publisher(None)

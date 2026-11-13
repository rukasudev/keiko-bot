"""Fixtures of the soak: the real log handlers posting on the running loop, and a
guard that keeps the soak out of every run that did not ask for it."""

import asyncio
import logging

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip the soak unless the marker expression names it (`-m soak`)."""
    if "soak" in (config.getoption("-m") or "").replace("not soak", ""):
        return
    skip = pytest.mark.skip(reason="the soak runs only with -m soak (make soak)")
    for item in items:
        if item.get_closest_marker("soak"):
            item.add_marker(skip)


@pytest.fixture
def streamer_commands(monkeypatch):
    """Every soak server's StreamElements streamer answers `mouse`."""
    from app.integrations.stream_elements import StreamElementsClient

    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(
            lambda _channel_id: [
                {"command": "mouse", "reply": "Logitech G Pro", "enabled": True}
            ]
        ),
    )


@pytest.fixture
async def soak_logging(monkeypatch):
    """The handlers `LoggerHooks.start` and the bot install, with the journey
    published on the running loop at once, and pytest's own capture removed."""
    from app import logger as logger_module
    from app.constants import Commands
    from app.core import analytics, journey
    from app.core import trace as trace_service
    from tests.behavioral.soak.traffic import admin_bot

    root = logging.getLogger()
    captured = [
        handler
        for handler in root.handlers
        if type(handler).__module__.startswith("_pytest")
    ]
    for handler in captured:
        root.removeHandler(handler)
    monkeypatch.setattr(Commands, "ANALYTICS_JOURNEY_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(Commands, "MEMBERS_RECENT_SIZE", 50)

    bot = admin_bot(asyncio.get_running_loop())
    folding = logger_module.TraceFoldingHandler()
    root.addHandler(folding)
    stored = logger_module.StoredLogsHandler()
    discord_handler = logger_module.DiscordLogsHandler(bot)
    yield bot

    for handler in (discord_handler, stored, folding):
        root.removeHandler(handler)
    for handler in captured:
        root.addHandler(handler)
    trace_service.clear_sinks()
    journey.clear()
    journey.set_publisher(None)
    journey.set_recovery_listener(None)
    analytics.clear_observers()

"""The heartbeat: a monitor learns the bot stopped, Grafana learns it is late.

`record_loop_lag` existed with no caller, so `keiko_event_loop_lag_seconds` was
never set, and nothing outside the process would notice it stop or freeze.

Found in review of the first cut: the lag was the gap between two ticks minus
the interval. discord.py anchors a relative loop to its schedule, so a loop that
is always three seconds late still ticks sixty seconds apart, and read zero.
Found in the second review: a gauge keeps only the last tick, and after a freeze
discord.py runs the missed ticks back to back, so a second later the gauge read
an on-time tick again and the freeze was gone from the metric.

Guaranteed: the cog ticks every `Commands.HEARTBEAT_SECONDS`; every tick is
counted with how late it woke up against the time it was scheduled for,
however late the tick before it was, and a freeze stays on the metric after the
loop catches up; with a `HEARTBEAT_URL` it pings that URL off the event loop,
and without one, or while the gateway is disconnected, it pings nothing; a
failing ping never stops the loop, never posts to Discord and never writes the
URL (it carries the monitor's token) into a log, not even at DEBUG; loading the
cog exposes the running version.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest
import requests
from prometheus_client import REGISTRY

from app import logger as logger_module
from app.cogs import heartbeat as heartbeat_module
from app.constants import Commands
from app.services import debug_logs, metrics

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("heartbeat")]

URL = "https://hc-ping.example/0f1e2d3c-monitor-token"
START = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def heartbeat(url="", version="v1.2.3", **bot):
    config = SimpleNamespace(HEARTBEAT_URL=url, APP_VERSION=version)
    return heartbeat_module.Heartbeat(SimpleNamespace(config=config, **bot))


async def tick(cog):
    await heartbeat_module.Heartbeat.beat.coro(cog)


def scheduled_at(cog, scheduled):
    """What the running loop reports during the tick it scheduled for `scheduled`."""
    interval = timedelta(seconds=Commands.HEARTBEAT_SECONDS)
    cog.beat = SimpleNamespace(
        next_iteration=scheduled + interval, seconds=interval.total_seconds()
    )


def lag_sample(suffix, **labels):
    name = f"keiko_event_loop_lag_seconds_{suffix}"
    return REGISTRY.get_sample_value(name, labels) or 0.0


def ticks_later_than(seconds):
    """How many ticks the metric holds that woke up more than `seconds` late."""
    return lag_sample("count") - lag_sample("bucket", le=str(seconds))


def answered(url, timeout):
    return SimpleNamespace(raise_for_status=lambda: None)


def test_the_heartbeat_ticks_every_minute():
    assert heartbeat_module.Heartbeat.beat.seconds == Commands.HEARTBEAT_SECONDS == 60


async def test_two_ticks_late_in_a_row_both_read_late(monkeypatch):
    readings = []
    monkeypatch.setattr(metrics, "record_loop_lag", readings.append)
    cog = heartbeat()

    for index in range(2):
        scheduled = START + timedelta(seconds=Commands.HEARTBEAT_SECONDS * index)
        scheduled_at(cog, scheduled)
        woke = scheduled + timedelta(seconds=3)
        monkeypatch.setattr(discord.utils, "utcnow", lambda woke=woke: woke)

        await tick(cog)

    assert readings == [3.0, 3.0], "each tick woke three seconds after its time"


async def test_an_early_tick_counts_as_on_time(monkeypatch):
    cog = heartbeat()
    scheduled_at(cog, START)
    monkeypatch.setattr(
        discord.utils, "utcnow", lambda: START - timedelta(milliseconds=4)
    )
    on_time, total = lag_sample("bucket", le="0.25"), lag_sample("sum")

    await tick(cog)

    assert lag_sample("bucket", le="0.25") == on_time + 1
    assert lag_sample("sum") == total, "an early tick adds no lag"


async def test_a_freeze_stays_on_the_metric_after_the_loop_catches_up():
    """The real loop, frozen once by a blocking call, then left to catch up."""
    ready = asyncio.Event()
    ready.set()
    cog = heartbeat(wait_until_ready=ready.wait)
    cog.beat.change_interval(seconds=0.05)
    late_before = ticks_later_than(0.25)

    await cog.cog_load()
    try:
        await asyncio.sleep(0.12)
        time.sleep(0.5)
        await asyncio.sleep(0.5)
    finally:
        await cog.cog_unload()
        await asyncio.sleep(0)

    assert ticks_later_than(0.25) > late_before, (
        "the ticks discord.py ran back to back after the freeze must not erase it"
    )


async def test_on_the_running_loop_a_tick_on_time_reads_no_lag(monkeypatch):
    """Pins what the lag is read against: during a tick, discord.py's
    `next_iteration` is the next tick's time, one interval after this one's."""
    readings = []
    monkeypatch.setattr(metrics, "record_loop_lag", readings.append)
    ready = asyncio.Event()
    ready.set()
    cog = heartbeat(wait_until_ready=ready.wait)

    await cog.cog_load()
    try:
        for _ in range(100):
            if readings:
                break
            await asyncio.sleep(0.01)
    finally:
        await cog.cog_unload()
        await asyncio.sleep(0)

    assert readings, "the first tick runs as soon as the bot is ready"
    assert 0 <= readings[0] < 1


async def test_without_a_url_nothing_is_pinged(monkeypatch):
    pinged = []
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: pinged.append(args))

    await tick(heartbeat(url=""))

    assert pinged == []


async def test_with_a_url_each_tick_pings_it_with_a_timeout(monkeypatch):
    pinged = []

    def get(url, timeout):
        pinged.append((url, timeout))
        return answered(url, timeout)

    monkeypatch.setattr(requests, "get", get)

    await tick(heartbeat(url=URL))

    assert pinged == [(URL, Commands.HEARTBEAT_TIMEOUT_SECONDS)]


async def test_a_disconnected_gateway_is_never_reported_alive(monkeypatch):
    pinged = []
    monkeypatch.setattr(
        requests, "get", lambda url, timeout: pinged.append(url) or answered(url, timeout)
    )
    cog = heartbeat(url=URL)

    await cog.on_disconnect()
    await tick(cog)

    assert pinged == [], "the monitor must hear silence while the gateway is down"

    await cog.on_connected()
    await tick(cog)

    assert pinged == [URL]


def test_the_connection_follows_the_gateway_events():
    listeners = sorted(name for name, _ in heartbeat_module.Heartbeat.__cog_listeners__)

    assert listeners == ["on_disconnect", "on_ready", "on_resumed"]


async def test_a_failing_ping_never_stops_the_loop_nor_reaches_discord(
    monkeypatch, log_channels
):
    def unreachable(url, timeout):
        raise requests.ConnectionError(f"cannot reach {url}")

    monkeypatch.setattr(requests, "get", unreachable)
    stored = logger_module.StoredLogsHandler()
    try:
        await tick(heartbeat(url=URL))
    finally:
        logging.getLogger().removeHandler(stored)

    posted = log_channels.logs.embeds + log_channels.errors.embeds + log_channels.calls.embeds
    assert posted == [], "the monitor alerts on a missing ping; the channel need not"
    lines = [document["message"] for document in debug_logs.drain()]
    assert any("Heartbeat ping failed: ConnectionError" in line for line in lines)
    assert not any("monitor-token" in line for line in lines), "the URL is a secret"


async def test_a_ping_the_monitor_refuses_is_a_failure_too(monkeypatch, log_channels):
    def refused(url, timeout):
        response = requests.Response()
        response.status_code = 404
        return response

    monkeypatch.setattr(requests, "get", refused)
    stored = logger_module.StoredLogsHandler()
    try:
        await tick(heartbeat(url=URL))
    finally:
        logging.getLogger().removeHandler(stored)

    lines = [document["message"] for document in debug_logs.drain()]
    assert any("Heartbeat ping failed: HTTPError" in line for line in lines)


def test_debug_logging_never_writes_the_heartbeat_url():
    """urllib3 logs each request line, token included, at DEBUG, and `DEBUG=true`
    turns the root logger to DEBUG for the file uploaded to Discord every day."""
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.DEBUG)
    try:
        assert not logging.getLogger("urllib3.connectionpool").isEnabledFor(logging.DEBUG)
    finally:
        root.setLevel(level)


async def test_loading_the_cog_exposes_the_running_version():
    cog = heartbeat(version="v9.8.7", wait_until_ready=asyncio.Event().wait)

    await cog.cog_load()
    try:
        assert REGISTRY.get_sample_value("keiko_build_info", {"version": "v9.8.7"}) == 1.0
    finally:
        await cog.cog_unload()
        await asyncio.sleep(0)

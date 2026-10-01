"""Stopping the container closes the bot and writes what it queued.

Deploys removed the container with `docker rm -f`, a SIGKILL: the bot never
closed, and the analytics events and log records still in their queues were
lost. A plain `docker stop` would not have helped, because Python leaves
SIGTERM at its default and a container's PID 1 ignores a signal left at its
default, so Docker waited and killed it the same way.

`app/lifecycle.py` turns SIGTERM into `close()` on the bot's own loop, writes
both queues once the loop is gone, and ends the process: the webhook API runs
on a non-daemon thread that would otherwise keep a stopped bot alive until
Docker's SIGKILL. A bot that closes itself (`/admin shutdown`) still writes
its queues but keeps the process, because an exit would make Docker's restart
policy bring it straight back; a later SIGTERM ends it at once, and one that
arrives during the writes or while a failure is logged waits for them. A bot
that fails to run used to skip the writes and leave the process up with no
bot, so the container looked healthy and the restart policy never fired: it
writes them and exits with 1, or with 0 when a stop was asked meanwhile.

A start Discord refuses (a bad token, a privileged intent the portal does not
grant) never clears by itself, and the restart policy retries it about once a
minute; every retry of a missing intent sends an IDENTIFY, and Discord resets
the token after 1,000 of them in a day. Such a failure waits
`FATAL_START_BACKOFF_SECONDS` before exiting with 1, and a SIGTERM during the
wait ends it at once with 0.

The patched `os._exit` records each exit with the number of events and logs
written by then, so an exit before the writes fails here, and raises like the
real one never returns. The tests send a real SIGTERM to the test process
while a fake bot runs.
"""

import asyncio
import logging
import os
import signal
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from app import lifecycle
from app.constants import DBConfigs, LogTypes
from app.data import logs as logs_data
from app.services import analytics, analytics_sink, debug_logs
from tests.behavioral.contracts.test_logger_trace import discord_logs  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("lifecycle")]

REFUSED_STARTS = [
    discord.LoginFailure("Improper token has been passed."),
    discord.PrivilegedIntentsRequired(None),
    discord.ConnectionClosed(
        SimpleNamespace(close_code=4011), shard_id=None, code=4011
    ),
]


class ProcessEnded(BaseException):
    """What the patched `os._exit` raises: like the real one, it never returns."""


def _send_sigterm():
    handler = signal.getsignal(signal.SIGTERM)
    assert handler not in (signal.SIG_DFL, signal.SIG_IGN), (
        "nothing handles SIGTERM, so sending it would end the test run"
    )
    os.kill(os.getpid(), signal.SIGTERM)


def _sigterm_without_interrupting(moment):
    try:
        _send_sigterm()
    except KeyboardInterrupt:
        pytest.fail(f"a SIGTERM {moment} raised KeyboardInterrupt")


def _sigterm_at_first_line(when):
    """A tracer that runs the installed SIGTERM handler at the first line of
    `lifecycle.run` where `when()` holds, as a signal arriving there would."""

    def tracer(frame, event, arg):
        if frame.f_code is not lifecycle.run.__code__:
            return None

        def line(frame, event, arg):
            if event == "line" and when():
                sys.settrace(None)
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return line

        return line

    return tracer


class FakeBot:
    """The part of discord.py's bot the lifecycle touches: listeners, `loop`, `run`."""

    loop = discord.utils.MISSING

    def __init__(self):
        self.listeners = {}
        self.returned = False

    def add_listener(self, listener, name):
        self.listeners.setdefault(name, []).append(listener)

    def remove_listener(self, listener, name):
        if listener in self.listeners.get(name, []):
            self.listeners[name].remove(listener)

    async def dispatch(self, name):
        for listener in list(self.listeners.get(name, [])):
            await listener()


class RunningBot(FakeBot):
    """A bot that serves until a SIGTERM closes it on its own loop."""

    def __init__(self):
        super().__init__()
        self.served_on = None
        self.closed_on = None
        self.closes = 0
        self._closed = None

    def run(self, token, *, reconnect):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._serve())
        finally:
            loop.close()

    async def _serve(self):
        self.loop = self.served_on = asyncio.get_running_loop()
        self._closed = asyncio.Event()

        _send_sigterm()
        _send_sigterm()

        await asyncio.wait_for(self._closed.wait(), timeout=2)
        await asyncio.sleep(0.01)

    async def close(self):
        self.closes += 1
        self.closed_on = asyncio.get_running_loop()
        self._closed.set()
        self.loop = discord.utils.MISSING


class FailsWhileStoppingBot(RunningBot):
    """A bot whose run raises after a SIGTERM has asked it to stop."""

    async def _serve(self):
        self.loop = self.served_on = asyncio.get_running_loop()
        self._closed = asyncio.Event()

        _send_sigterm()

        await asyncio.wait_for(self._closed.wait(), timeout=2)
        raise RuntimeError("the gateway closed badly")


class StartingBot(FakeBot):
    """A bot that receives SIGTERM before its loop exists."""

    def run(self, token, *, reconnect):
        _send_sigterm()
        raise AssertionError("the SIGTERM did not stop a bot that was starting")


class SelfClosingBot(FakeBot):
    """A bot that closed itself and returned, as `/admin shutdown` makes it."""

    def run(self, token, *, reconnect):
        self.returned = True


class ReadyBot(FakeBot):
    """A bot that becomes ready twice, as a reconnect makes it, then closes itself."""

    def run(self, token, *, reconnect):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._become_ready_twice())
        finally:
            loop.close()

    async def _become_ready_twice(self):
        await self.dispatch("on_ready")
        await self.dispatch("on_ready")


class ReenteredBot(FakeBot):
    """A bot whose SIGTERM handler is entered again at line `at` of what its first call
    runs, as a second SIGTERM arriving there would. Both run on a worker thread, so a
    handler that blocks on itself fails the test instead of hanging it."""

    def __init__(self, at):
        super().__init__()
        self.at = at
        self.lines = 0
        self.handled = False
        self.closes = 0

    def run(self, token, *, reconnect):
        self.loop = asyncio.new_event_loop()
        handler = signal.getsignal(signal.SIGTERM)
        worker = threading.Thread(target=self._handle, args=(handler,), daemon=True)
        worker.start()
        worker.join(timeout=5)
        try:
            self.loop.run_until_complete(asyncio.sleep(0))
        finally:
            self.loop.close()

    def _handle(self, handler):
        def line(frame, event, arg):
            if event == "line":
                self.lines += 1
                if self.lines == self.at:
                    handler(signal.SIGTERM, None)
            return line

        sys.settrace(lambda frame, event, arg: line)
        try:
            handler(signal.SIGTERM, None)
        finally:
            sys.settrace(None)
        self.handled = True

    async def close(self):
        self.closes += 1


class FailingBot(FakeBot):
    """A bot whose run raises `failure`, as a bad token or a broken `setup_hook` do."""

    def __init__(self, failure):
        super().__init__()
        self.failure = failure

    def run(self, token, *, reconnect):
        raise self.failure


@pytest.fixture
def sigterm():
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


@pytest.fixture
def stored(monkeypatch):
    written = SimpleNamespace(events=[], logs=[], exits=[])
    monkeypatch.setattr(analytics_sink, "persist", written.events.extend)
    monkeypatch.setattr(logs_data, "insert_logs", written.logs.extend)

    def end_process(code):
        written.exits.append((code, len(written.events), len(written.logs)))
        raise ProcessEnded(code)

    monkeypatch.setattr(os, "_exit", end_process)
    return written


@pytest.fixture
def backoff(monkeypatch):
    """The fatal-start wait, recorded instead of slept, with `during` run inside it,
    so a test acts at the moment the wait has begun rather than racing a timer."""
    wait = SimpleNamespace(seconds=[], during=lambda: None)

    def sleep(seconds):
        wait.seconds.append(seconds)
        wait.during()

    monkeypatch.setattr(lifecycle.time, "sleep", sleep)
    return wait


@pytest.fixture
def console(capfd):
    """Starts the handlers `LoggerHooks.start` installs, so the console writes to the
    captured stdout; started inside the test, where the capture stays open."""
    from app.logger import LoggerHooks

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    config = SimpleNamespace(
        is_debug=lambda: False, is_prod=lambda: False, DEBUG_LOGS_ENABLED=True
    )

    def start():
        LoggerHooks(config, file_logs=False).start()
        return capfd

    yield start
    for handler in root.handlers[:]:
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


def _queue_one_of_each():
    analytics.emit("guild.joined", guild_id=1, returning=False)
    debug_logs.record(debug_logs.build_document(level="INFO", message="closing"))


def _failures_logged(caplog, name):
    return [record.levelno for record in caplog.records if name in record.getMessage()]


def test_a_sigterm_closes_the_bot_and_writes_both_queues_before_the_process_ends(
    sigterm, stored
):
    _queue_one_of_each()
    bot = RunningBot()

    with pytest.raises(ProcessEnded):
        lifecycle.run(bot, "token")

    assert bot.closed_on is bot.served_on, "the bot closes on its own loop"
    assert bot.closes == 1, "a second SIGTERM does not close it twice"
    assert [event["event"] for event in stored.events] == ["guild.joined"]
    assert [log["message"] for log in stored.logs] == ["closing"]
    assert stored.exits == [(0, 1, 1)], "the process ends with 0, after both writes"


def test_a_sigterm_while_the_bot_is_starting_still_writes_both_queues(sigterm, stored):
    _queue_one_of_each()

    with pytest.raises(ProcessEnded):
        lifecycle.run(StartingBot(), "token")

    assert stored.exits == [(0, 1, 1)]


def test_a_bot_that_closes_itself_writes_its_queues_and_keeps_the_process(
    sigterm, stored
):
    _queue_one_of_each()

    lifecycle.run(SelfClosingBot(), "token")

    assert len(stored.events) == 1 and len(stored.logs) == 1
    assert stored.exits == [], "only a SIGTERM or a failure ends the process"


def test_a_sigterm_after_a_self_close_ends_the_process_at_once(sigterm, stored):
    _queue_one_of_each()
    lifecycle.run(SelfClosingBot(), "token")

    with pytest.raises(ProcessEnded):
        _sigterm_without_interrupting("after the bot closed itself")

    assert stored.exits == [(0, 1, 1)]


def test_a_sigterm_during_the_final_writes_waits_for_them(sigterm, stored, monkeypatch):
    _queue_one_of_each()

    def persist(batch):
        stored.events.extend(batch)
        _sigterm_without_interrupting("during the final writes")

    monkeypatch.setattr(analytics_sink, "persist", persist)

    with pytest.raises(ProcessEnded):
        lifecycle.run(SelfClosingBot(), "token")

    assert stored.exits == [(0, 1, 1)], "the writes finish, then the process ends"


def test_a_bot_that_fails_to_run_writes_its_queues_and_exits_with_1_at_once(
    sigterm, stored, backoff, caplog
):
    _queue_one_of_each()

    with pytest.raises(ProcessEnded):
        lifecycle.run(FailingBot(RuntimeError("setup_hook broke")), "token")

    assert stored.exits == [(1, 1, 1)], "a failed bot ends the process for a restart"
    assert backoff.seconds == [], "only a start Discord refused waits"
    assert _failures_logged(caplog, "RuntimeError") == [logging.ERROR]


def test_a_sigterm_while_a_failure_is_logged_never_interrupts_the_writes(
    sigterm, stored
):
    _queue_one_of_each()
    interrupted = []

    class StopWhileLogging(logging.Handler):
        def emit(self, record):
            try:
                _send_sigterm()
            except KeyboardInterrupt:
                interrupted.append(record.getMessage())

    handler = StopWhileLogging(level=logging.WARNING)
    logging.getLogger().addHandler(handler)
    try:
        with pytest.raises(ProcessEnded):
            lifecycle.run(FailingBot(RuntimeError("setup_hook broke")), "token")
    finally:
        logging.getLogger().removeHandler(handler)

    assert interrupted == [], "the SIGTERM raised KeyboardInterrupt out of the log call"
    assert stored.exits == [(0, 1, 1)], "the stop was asked: 0, after the writes"


def test_a_failure_after_a_stop_was_asked_exits_with_0_and_only_warns(
    sigterm, stored, caplog
):
    _queue_one_of_each()

    with pytest.raises(ProcessEnded):
        lifecycle.run(FailsWhileStoppingBot(), "token")

    assert stored.exits == [(0, 1, 1)], "a stop that was asked ends with 0"
    assert _failures_logged(caplog, "RuntimeError") == [logging.WARNING]


@pytest.mark.parametrize(
    "refusal", REFUSED_STARTS, ids=lambda error: type(error).__name__
)
def test_a_start_discord_refuses_waits_the_backoff_before_exiting_with_1(
    sigterm, stored, backoff, caplog, refusal
):
    _queue_one_of_each()
    exits_during_the_wait = []
    backoff.during = lambda: exits_during_the_wait.append(list(stored.exits))

    with pytest.raises(ProcessEnded):
        lifecycle.run(FailingBot(refusal), "token")

    assert backoff.seconds == [DBConfigs.FATAL_START_BACKOFF_SECONDS]
    assert exits_during_the_wait == [[]], "the process waits before it exits"
    assert stored.exits == [(1, 1, 1)]
    assert _failures_logged(caplog, type(refusal).__name__) == [logging.CRITICAL]


@pytest.mark.parametrize(
    "refusal", REFUSED_STARTS, ids=lambda error: type(error).__name__
)
def test_a_refused_start_is_one_critical_console_line_the_deploy_check_finds(
    sigterm, stored, backoff, console, refusal
):
    """What `docker logs keiko-bot` shows, and what the deploy check looks for."""
    captured = console()

    with pytest.raises(ProcessEnded):
        lifecycle.run(FailingBot(refusal), "token")

    out, err = captured.readouterr()
    assert "Logging error" not in err, err
    phrase = DBConfigs.FATAL_START_PHRASE
    lines = [line for line in (out + err).splitlines() if phrase in line]
    assert lines, f"no console line says {phrase!r}"
    assert len(lines) == 1 and "CRITICAL" in lines[0]
    assert lines[0].endswith(f"{phrase}: {type(refusal).__name__}: {refusal}")


def test_a_sigterm_during_the_fatal_start_backoff_exits_with_0_at_once(
    sigterm, stored, backoff
):
    _queue_one_of_each()

    def stop_during_the_wait():
        analytics.emit("guild.joined", guild_id=2, returning=False)
        _send_sigterm()

    backoff.during = stop_during_the_wait

    with pytest.raises(ProcessEnded):
        lifecycle.run(FailingBot(discord.PrivilegedIntentsRequired(None)), "token")

    assert stored.exits == [(0, 1, 1)], "0 at once, and nothing written after the wait"


def test_a_sigterm_during_the_write_after_the_backoff_lets_it_finish_and_exits_with_0(
    sigterm, stored, backoff, monkeypatch
):
    _queue_one_of_each()

    def logged_during_the_wait():
        analytics.emit("guild.joined", guild_id=4, returning=False)
        debug_logs.record(debug_logs.build_document(level="INFO", message="waiting"))

    def persist(batch):
        stored.events.extend(batch)
        if backoff.seconds:
            _sigterm_without_interrupting("during the write after the backoff")

    backoff.during = logged_during_the_wait
    monkeypatch.setattr(analytics_sink, "persist", persist)

    with pytest.raises(ProcessEnded):
        lifecycle.run(FailingBot(discord.PrivilegedIntentsRequired(None)), "token")

    assert stored.exits == [(0, 2, 2)], "the write finishes, then the asked stop wins"


def test_a_bot_that_becomes_ready_says_so_once_on_the_console(sigterm, stored, console):
    """The deploy check waits for this line: it proves the bot logged in and
    received READY, which no amount of `running 0` does."""
    captured = console()

    lifecycle.run(ReadyBot(), "token")

    out, err = captured.readouterr()
    phrase = DBConfigs.READY_PHRASE
    lines = [line for line in (out + err).splitlines() if phrase in line]
    assert lines, f"no console line says {phrase!r}"
    assert len(lines) == 1, "a reconnect does not announce the bot again"
    assert "INFO" in lines[0] and lines[0].endswith(phrase)


def test_what_is_logged_during_the_fatal_start_backoff_is_written_before_the_exit(
    sigterm, stored, backoff
):
    _queue_one_of_each()
    backoff.during = lambda: analytics.emit("guild.joined", guild_id=3, returning=False)

    with pytest.raises(ProcessEnded):
        lifecycle.run(FailingBot(discord.PrivilegedIntentsRequired(None)), "token")

    assert stored.exits == [(1, 2, 1)], "the event logged during the wait is written"


def test_the_ready_line_is_stored_once_and_never_posted_to_discord(
    sigterm, stored, discord_logs
):
    """The events cog already posts the startup message to the admin channel. The
    ready line is for the console, `guild.logs` and the deploy check, so it is logged
    inside a quiet trace: dropping `quiet=True` or the trace posts a second message."""
    from app.logger import StoredLogsHandler

    root = logging.getLogger()
    root.addHandler(discord_logs.handler)
    stored_logs = StoredLogsHandler(
        SimpleNamespace(DEBUG_LOGS_ENABLED=True, is_prod=lambda: False)
    )
    try:
        lifecycle.run(ReadyBot(), "token")
    finally:
        root.removeHandler(discord_logs.handler)
        root.removeHandler(stored_logs)

    ready = [
        (log["level"], log["log_type"])
        for log in stored.logs
        if log["message"] == DBConfigs.READY_PHRASE
    ]
    assert discord_logs.sends == [], "the events cog posts the only startup message"
    assert ready == [("INFO", LogTypes.APPLICATION_STARTUP_TYPE)]


def _run_with_a_sigterm_at(bot, when):
    previous = sys.gettrace()
    sys.settrace(_sigterm_at_first_line(when))
    try:
        with pytest.raises(ProcessEnded):
            lifecycle.run(bot, "token")
    except KeyboardInterrupt:
        pytest.fail("the SIGTERM escaped lifecycle.run as KeyboardInterrupt")
    finally:
        sys.settrace(previous)


def test_a_second_sigterm_inside_the_first_one_never_blocks_its_handler(
    sigterm, stored
):
    """A signal handler runs again when the next signal lands at any line it runs,
    the lines of what it calls included; one that holds a lock there, as
    `threading.Event.set` does, then waits forever on itself."""
    at = 0
    while True:
        at += 1
        for written in (stored.events, stored.logs, stored.exits):
            written.clear()
        _queue_one_of_each()
        bot = ReenteredBot(at)

        try:
            lifecycle.run(bot, "token")
        except ProcessEnded:
            pass

        if bot.lines < at:
            break
        assert bot.handled, f"a second SIGTERM at line {at} of the handler blocked it"
        assert bot.closes >= 1
        assert stored.exits == [(0, 1, 1)]

    assert at > 3, "the second SIGTERM reached past the handler's own lines"


def test_a_sigterm_just_after_the_stop_handler_is_installed_still_writes(
    sigterm, stored
):
    """No window between installing the handler that closes the bot and the
    block that turns its KeyboardInterrupt into a clean stop."""
    _queue_one_of_each()
    before = signal.getsignal(signal.SIGTERM)

    _run_with_a_sigterm_at(
        SelfClosingBot(), lambda: signal.getsignal(signal.SIGTERM) is not before
    )

    assert stored.exits == [(0, 1, 1)]


def test_a_sigterm_just_after_the_bot_returns_still_writes(sigterm, stored):
    """No window between the bot returning and the handler that only records."""
    _queue_one_of_each()
    bot = SelfClosingBot()
    before = signal.getsignal(signal.SIGTERM)
    first_installed = []

    def just_returned():
        installed = signal.getsignal(signal.SIGTERM)
        if installed is not before and not first_installed:
            first_installed.append(installed)
        return bot.returned and installed is first_installed[0]

    _run_with_a_sigterm_at(bot, just_returned)

    assert stored.exits == [(0, 1, 1)]


async def test_unloading_the_analytics_cog_writes_both_queues(stored):
    from app.cogs.analytics import Analytics

    _queue_one_of_each()
    loops = SimpleNamespace(
        flush_events=MagicMock(),
        send_weekly_digest=MagicMock(),
        export_daily_logs=MagicMock(),
    )

    await Analytics.cog_unload(loops)

    assert len(stored.events) == 1 and len(stored.logs) == 1
    assert stored.exits == []


def test_a_sigterm_before_the_loop_exists_stops_the_way_ctrl_c_does():
    starting = discord.Client(intents=discord.Intents.none())

    with pytest.raises(KeyboardInterrupt):
        lifecycle.stop(starting)

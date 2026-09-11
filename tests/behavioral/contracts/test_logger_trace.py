"""Log aggregation contract: one unit of work is one Discord message.

Before this suite `app/logger.py` had no coverage at all, and a webhook that
logged seven times produced seven messages in an order that did not match the
order things happened. Everything below pins the behavior that replaced it.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import logger as logger_module
from app.constants import LogTypes as logconstants
from app.services import trace as trace_service
from app.services.trace import Trace, trace_scope

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("logging")]


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


def rendered(embed):
    """Everything the reader sees, wherever the layout puts it.

    These tests pin the contract (one unit of work is one message, in order),
    not whether a line lives in the description or in a field.
    """
    parts = [embed.title or "", embed.description or ""]
    parts += [f"{field.name} {field.value}" for field in embed.fields]
    parts.append(embed.footer.text or "")
    return "\n".join(parts)


def make_record(message, level=logging.INFO, **extra):
    record = logging.LogRecord(
        name="keiko", level=level, pathname="app/webhooks/reminder.py",
        lineno=1, msg=message, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_seven_records_in_one_trace_become_one_message(discord_logs):
    with trace_scope("reminder", source="webhook") as trace:
        for index in range(7):
            discord_logs.emit(make_record(f"step {index}"))
        assert discord_logs.sends == []

    assert len(discord_logs.sends) == 1
    embed = discord_logs.sends[0]["embed"]
    for index in range(7):
        assert f"step {index}" in rendered(embed)


def test_the_timeline_keeps_the_order_things_happened(discord_logs):
    with trace_scope("reminder", source="webhook"):
        discord_logs.emit(make_record("webhook received"))
        discord_logs.emit(make_record("renewing subscription"))
        discord_logs.emit(make_record("renewal scheduled"))

    description = rendered(discord_logs.sends[0]["embed"])
    assert description.index("webhook received") < description.index("renewing subscription")
    assert description.index("renewing subscription") < description.index("renewal scheduled")


def test_records_outside_a_trace_still_get_their_own_message(discord_logs):
    discord_logs.emit(make_record("standalone"))

    assert len(discord_logs.sends) == 1
    assert discord_logs.sends[0]["embed"].description == "standalone"


def test_errors_keep_a_message_of_their_own_and_stay_in_the_timeline(discord_logs):
    with trace_scope("reminder", source="webhook"):
        discord_logs.emit(make_record("starting"))
        discord_logs.emit(
            make_record("boom", level=logging.ERROR,
                        log_type=logconstants.COMMAND_ERROR_TYPE)
        )

    assert len(discord_logs.sends) == 2
    trace_embed = discord_logs.sends[-1]["embed"]
    assert "boom" in rendered(trace_embed)
    assert "starting" in rendered(trace_embed)


def test_a_failed_trace_is_marked_as_such(discord_logs):
    with trace_scope("reminder", source="webhook"):
        discord_logs.emit(
            make_record("boom", level=logging.ERROR,
                        log_type=logconstants.COMMAND_ERROR_TYPE)
        )

    trace_embed = discord_logs.sends[-1]["embed"]
    assert logconstants.TRACE_RESULT_FAILURE in rendered(trace_embed)


def test_a_clean_silent_trace_never_reaches_discord(discord_logs):
    with trace_scope("on_message", source="internal", silent_when_clean=True):
        discord_logs.emit(make_record("checked a message"))

    assert discord_logs.sends == []


def test_a_silent_trace_that_fails_does_reach_discord(discord_logs):
    with trace_scope("on_message", source="internal", silent_when_clean=True):
        discord_logs.emit(
            make_record("boom", level=logging.ERROR,
                        log_type=logconstants.COMMAND_ERROR_TYPE)
        )

    assert any("embed" in send for send in discord_logs.sends)


def test_a_silent_trace_that_reported_an_event_does_reach_discord(discord_logs):
    """Silence protects `on_message` from flooding, not the guild lifecycle.

    A listener trace publishes nothing unless it failed, and `on_guild_remove`
    never fails: the "Left Guild" line was written to `guild.logs` and dropped
    on its way to the channel that exists for it.
    """
    with trace_scope("on_guild_remove", source="internal", silent_when_clean=True):
        discord_logs.emit(make_record(
            "Left guild by 313", guild_id="42",
            log_type=logconstants.EVENT_LEFT_GUILD_TYPE,
        ))

    assert len(discord_logs.sends) == 1
    embed = discord_logs.sends[0]["embed"]
    assert embed.title == logconstants.EVENT_LEFT_GUILD_TITLE, (
        "`👂 Listener Event` is not what someone scanning the channel looks for"
    )
    assert "Left guild by 313" in rendered(embed)
    assert "42" in rendered(embed), "the message has to say which guild left"


def test_an_event_line_keeps_its_trace_even_when_the_timeline_overflows(discord_logs):
    """What publishes the message is that the event happened, not that it fit."""
    with trace_scope("on_guild_join", source="internal", silent_when_clean=True):
        for index in range(logconstants.TRACE_MAX_LINES + 3):
            discord_logs.emit(make_record(f"noise {index}"))
        discord_logs.emit(make_record(
            "Joined new guild", log_type=logconstants.EVENT_JOIN_GUILD_TYPE,
        ))

    assert len(discord_logs.sends) == 1
    assert discord_logs.sends[0]["embed"].title == logconstants.EVENT_JOIN_GUILD_TITLE


def test_a_repeated_attempt_is_called_out_on_the_message_you_already_read(
    discord_logs,
):
    """The "why did they run it again" question, answered in place instead of
    by correlating two log messages by hand."""
    with trace_scope("moderations block links", user_id="9", guild_id="1",
                     source="slash") as trace:
        trace.footnote = "⚠️ 2º run of `block_links` by this guild in the last 24h"
        discord_logs.emit(make_record("`/moderations block links` started"))

    message = rendered(discord_logs.sends[-1]["embed"])
    assert "2º run of `block_links`" in message
    assert "`/moderations block links` started" in message


def test_a_first_attempt_carries_no_footnote():
    from app.services import analytics

    assert analytics.describe_attempt(1, "block_links") is None
    assert analytics.describe_attempt(2, "block_links")


def test_counting_an_attempt_never_breaks_the_command_it_measures():
    from app.services import analytics

    assert analytics.count_attempt(None, "block_links") == 0
    assert analytics.count_attempt("1", None) == 0


def test_the_trace_timeline_is_capped_so_a_loop_cannot_flood_an_embed():
    trace = Trace("runaway")
    for index in range(logconstants.TRACE_MAX_LINES + 25):
        trace.add(f"line {index}")

    assert len(trace.lines) == logconstants.TRACE_MAX_LINES
    assert trace.truncated == 25


def test_long_lines_are_trimmed_instead_of_breaking_the_embed():
    trace = Trace("verbose")
    trace.add("x" * 5000)

    assert len(trace.lines[0]["message"]) <= logconstants.TRACE_LINE_MAX_LENGTH


def test_nested_scopes_share_one_timeline(discord_logs):
    with trace_scope("outer", source="webhook"):
        discord_logs.emit(make_record("outer line"))
        with trace_scope("inner", source="webhook"):
            discord_logs.emit(make_record("inner line"))
        assert discord_logs.sends == []

    assert len(discord_logs.sends) == 1
    description = rendered(discord_logs.sends[0]["embed"])
    assert "outer line" in description
    assert "inner line" in description


def test_a_muted_record_never_becomes_a_trace_line(discord_logs):
    with trace_scope("gateway", source="internal") as trace:
        discord_logs.emit(make_record("We are being rate limited."))
        assert trace.lines == []


def test_user_traces_and_system_traces_go_to_different_channels(discord_logs):
    with trace_scope("moderations block links", user_id="9", guild_id="1", source="slash"):
        pass
    with trace_scope("reminder", source="webhook"):
        pass

    assert discord_logs.routed == ["calls", "logs"]


def test_errors_are_routed_to_the_error_channel(discord_logs):
    discord_logs.emit(
        make_record("boom", level=logging.ERROR,
                    log_type=logconstants.COMMAND_ERROR_TYPE)
    )

    assert discord_logs.routed == ["errors"]


def test_a_sink_that_explodes_never_breaks_the_traced_work():
    trace_service.clear_sinks()
    trace_service.register_sink(lambda trace: 1 / 0)

    with trace_scope("fragile", source="internal") as trace:
        trace.add("did the work")

    trace_service.clear_sinks()


def test_the_handler_formats_the_record_itself(discord_logs):
    """asctime only exists because the handler creates it, not because some
    other handler happened to run first."""
    record = make_record("hello")
    discord_logs.emit(record)

    assert hasattr(record, "asctime")
    assert discord_logs.sends[0]["embed"].footer.text.endswith(record.asctime)


def test_an_error_without_a_log_type_or_exception_still_reaches_discord(discord_logs):
    """`logger.error("something")` with neither log_type nor exc_info used to
    raise TypeError inside the handler (`record.exc_info[1]` on None), and
    logging swallows handler errors — so the one message that mattered most was
    the one that silently disappeared."""
    discord_logs.emit(make_record("plain failure", level=logging.ERROR))

    assert len(discord_logs.sends) == 1
    assert "plain failure" in discord_logs.sends[0]["embed"].description


def test_an_interaction_without_a_guild_does_not_crash_the_handler(discord_logs):
    interaction = SimpleNamespace(
        id=1, guild=None, user=SimpleNamespace(mention="<@9>"),
        channel=None, message=None, command=None,
    )
    discord_logs.emit(make_record("dm interaction", interaction=interaction))

    assert len(discord_logs.sends) == 1

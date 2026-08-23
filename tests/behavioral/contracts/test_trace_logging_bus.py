"""Everything a trace shows must have travelled through `logging`.

`DiscordLogsHandler.emit` already folds a log record into the open trace and
suppresses the separate embed, so `logger.info` inside a trace becomes a
timeline line *and* reaches the file and Mongo. Writing straight to
`trace.add()` skips that bus: the line renders in Discord and exists nowhere
else.

That is what happened in production. The invocation line was added with
`trace.add()` at two call sites, so `guild.logs` held eight boot records and
nothing about the commands people actually ran, while the log channel showed
the same invocation printed twice.
"""
import logging

import pytest

from app import logger as logger_module
from app.logger import StoredLogsHandler
from app.services import debug_logs
from app.services import trace as trace_service
from app.services.trace import trace_scope

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("logging")]


@pytest.fixture
def sink():
    """The bus as `LoggerHooks.start` wires it: folding first, then the sink."""
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)

    folding = logger_module.TraceFoldingHandler()
    root.addHandler(folding)
    stored = StoredLogsHandler()

    yield stored

    root.removeHandler(folding)
    root.removeHandler(stored)
    root.setLevel(previous_level)


def stored_messages():
    debug_logs.flush()
    from app.data import logs as logs_data

    return [document["message"] for document in logs_data.mongo_client.guild.logs.find({})]


# --------------------------------------------------------------------------
# One invocation, one line
# --------------------------------------------------------------------------

def test_a_nested_scope_does_not_repeat_the_opening_line(sink):
    """Broke as: the decorator and the button both added the same line, and a
    command reached through a button ran both, printing the invocation twice."""
    with trace_scope("moderations birthdays", opening="`/moderations birthdays` started") as outer:
        with trace_scope("moderations birthdays", opening="`/moderations birthdays` started"):
            pass

        openings = [line for line in outer.lines if "started" in line["message"]]

    assert len(openings) == 1, [line["message"] for line in outer.lines]


def test_the_opening_line_belongs_to_the_trace_that_was_created(sink):
    with trace_scope("ping", opening="`/ping` started") as trace:
        assert any("`/ping` started" in line["message"] for line in trace.lines)


def test_no_timeline_line_uses_the_word_the_style_guide_forbids(sink):
    """`invoke` is on the writing-style skill's avoid list."""
    with trace_scope("ping", opening="`/ping` started") as trace:
        pass

    for line in trace.lines:
        assert "invoke" not in line["message"].lower()


# --------------------------------------------------------------------------
# The hole this closes: the timeline must also be queryable
# --------------------------------------------------------------------------

def test_the_opening_line_reaches_the_stored_log(sink):
    """Broke as: production `guild.logs` had only boot records, because command
    activity never passed through `logging` at all."""
    with trace_scope("ping", opening="`/ping` started"):
        pass

    assert any("`/ping` started" in message for message in stored_messages())


def test_a_line_logged_inside_the_trace_is_both_folded_and_stored(sink):
    with trace_scope("moderations birthdays", opening="`/x` started") as trace:
        logging.getLogger("app.test").info("setup opened")

    assert any("setup opened" in line["message"] for line in trace.lines)
    assert any("setup opened" in message for message in stored_messages())


def test_a_trace_without_an_opening_logs_nothing_extra(sink):
    with trace_scope("on_message", silent_when_clean=True) as trace:
        pass

    assert trace.lines == []
    assert stored_messages() == []


@pytest.fixture(autouse=True)
def _clear_sinks():
    trace_service.clear_sinks()
    yield
    trace_service.clear_sinks()

"""traces.runs keeps the units of work worth reading again, and a command's latency.

Guaranteed: a trace that failed, logged an error or took longer than
`TRACE_RUNS_SLOW_MS` is kept in `traces.runs` with its bounded lines, by the same flush
that writes the events; a clean, quick trace is not; a superseded trace is never kept,
its lines live on in the trace that took them; a command's latency is counted from the
trace that closes it, once; a form session is kept when it failed or outlasted
`TRACE_RUNS_SLOW_SESSION_MS`.
"""
import logging
from datetime import timedelta

import pytest

from app.data import data, wait
from app.constants import Commands as constants
from app.core import analytics, journey, trace
from app.core.analytics_sink import now

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def kept_runs():
    trace.register_sink(trace.keep_run)


def closed(name="probe", *, command=None, error=False, slow_ms=0, result=None):
    with trace.trace_scope(name, guild_id="81", user_id="9", command=command) as unit:
        unit.started_at -= timedelta(milliseconds=slow_ms)
        if error:
            unit.add("the probe broke", logging.ERROR)
        if result:
            unit.result = result
    return unit


def stored(deps):
    wait(analytics.flush())
    return {run["trace_id"]: run for run in deps.mongo_client.traces.runs.find({})}


def test_a_failed_an_erroring_and_a_slow_trace_are_kept_and_a_clean_one_is_not(deps):
    failed = closed("failed", result=trace.constants.TRACE_RESULT_FAILURE)
    erroring = closed("erroring", error=True)
    slow = closed("slow", slow_ms=constants.TRACE_RUNS_SLOW_MS + 500)
    closed("clean")

    runs = stored(deps)

    assert set(runs) == {failed.id, erroring.id, slow.id}
    run = runs[erroring.id]
    assert run["name"] == "erroring" and run["guild_id"] == "81" and run["user_id"] == "9"
    assert run["result"] == trace.constants.TRACE_RESULT_FAILURE
    assert [line["message"] for line in run["lines"]] == ["the probe broke"]
    assert runs[slow.id]["duration_ms"] > constants.TRACE_RUNS_SLOW_MS


def test_a_superseded_trace_is_never_kept(deps):
    with trace.trace_scope("interaction", guild_id="81") as interaction:
        session = trace.Trace("session", guild_id="81")
        interaction.add("the probe broke", logging.ERROR)
        interaction.supersede(session)

    assert interaction.id not in stored(deps)


def test_a_commands_latency_is_counted_once_from_the_trace_that_closes_it(deps):
    closed("birthday", command="birthday")
    with trace.trace_scope("birthday", command="birthday") as first:
        successor = first.handover()
        successor.started_at -= timedelta(milliseconds=1200)
    trace.close(successor)

    stored(deps)

    moment = now()
    [document] = wait(data().analytics_keiko.find_counters([f"commands:{moment:%Y-%m}"]))
    assert document["days"][f"{moment:%d}"]["birthday"]["ms"] == {
        "0-250": 1, "1000-2000": 1,
    }


def test_a_failed_session_is_kept_and_a_quick_saved_one_is_not(deps):
    failed = journey.open_journey("broken", "birthday", guild_id="81")
    failed.add("the save broke", logging.ERROR)
    journey.finalize("broken", "failed")
    saved = journey.open_journey("saved", "birthday", guild_id="81")
    journey.finalize("saved", "saved")

    runs = stored(deps)

    assert failed.id in runs and saved.id not in runs


def test_two_runs_whose_trace_ids_collide_are_both_kept(deps, monkeypatch):
    """Broke as: a run was keyed by its trace id, six hex characters, and kept with
    `$setOnInsert`, so a second trace drawing the same id inside the 30 days the runs
    live was silently dropped. Guaranteed: every kept run is a document of its own (an
    ObjectId `_id`) that carries its trace id as a field."""
    monkeypatch.setattr(trace, "new_trace_id", lambda: "a1b2c3")
    closed("failed", result=trace.constants.TRACE_RESULT_FAILURE)
    closed("failed again", result=trace.constants.TRACE_RESULT_FAILURE)
    wait(analytics.flush())

    kept = list(deps.mongo_client.traces.runs.find({"trace_id": "a1b2c3"}))

    assert sorted(run["name"] for run in kept) == ["failed", "failed again"]

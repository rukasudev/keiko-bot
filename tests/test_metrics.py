"""Operational metrics: closed labels, never an id.

Grafana reads these, so a label that can take any value is a series per value,
and a guild or user id as a label is both a privacy leak and an unbounded
number of series. Every label here comes from a fixed list.
"""
from types import SimpleNamespace

import discord
import pytest
from prometheus_client import REGISTRY

from app.services import metrics

pytestmark = pytest.mark.unit


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def discord_error(code):
    return discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"),
        {"code": code, "message": "Unknown interaction"},
    )


def test_an_interaction_is_counted_with_the_code_discord_answered():
    before = sample("keiko_interactions_total", outcome="failed", code="10062")

    metrics.record_interaction("failed", discord_error(10062))

    assert sample("keiko_interactions_total", outcome="failed", code="10062") == before + 1


def test_a_failure_that_is_not_discords_is_counted_without_a_code():
    before = sample("keiko_interactions_total", outcome="failed", code="")

    metrics.record_interaction("failed", KeyError("allowed_links"))

    assert sample("keiko_interactions_total", outcome="failed", code="") == before + 1


def test_an_answer_in_time_carries_no_code():
    before = sample("keiko_interactions_total", outcome="in_time", code="")

    metrics.record_interaction("in_time")

    assert sample("keiko_interactions_total", outcome="in_time", code="") == before + 1


def test_every_tick_is_kept_not_only_the_last_one():
    """A late tick followed by an on-time one still shows the late one."""
    late = "keiko_event_loop_lag_seconds_bucket"
    before = sample("keiko_event_loop_lag_seconds_count") - sample(late, le="1.0")

    metrics.record_loop_lag(2.5)
    metrics.record_loop_lag(0.01)

    after = sample("keiko_event_loop_lag_seconds_count") - sample(late, le="1.0")
    assert after == before + 1


def test_a_tick_that_woke_early_counts_as_on_time():
    on_time = sample("keiko_event_loop_lag_seconds_bucket", le="0.25")
    total = sample("keiko_event_loop_lag_seconds_sum")

    metrics.record_loop_lag(-0.004)

    assert sample("keiko_event_loop_lag_seconds_bucket", le="0.25") == on_time + 1
    assert sample("keiko_event_loop_lag_seconds_sum") == total


def test_the_build_info_names_the_running_version():
    metrics.record_build_info("v1.2.3")

    assert sample("keiko_build_info", version="v1.2.3") == 1.0


def test_a_refused_webhook_request_is_counted_by_route_and_status():
    before = sample(
        "keiko_webhook_refusals_total", route="/v1/webhooks/reminder", status="401"
    )

    metrics.record_webhook_refusal("/v1/webhooks/reminder", 401)

    assert sample(
        "keiko_webhook_refusals_total", route="/v1/webhooks/reminder", status="401"
    ) == before + 1


def test_no_metric_is_labelled_by_a_guild_or_a_user():
    every_metric = (
        metrics.INTERACTIONS,
        metrics.LOOP_LAG,
        metrics.BUILD_INFO,
        metrics.WEBHOOK_REFUSALS,
    )
    for metric in every_metric:
        assert not {"guild_id", "user_id", "guild", "user"} & set(metric._labelnames)

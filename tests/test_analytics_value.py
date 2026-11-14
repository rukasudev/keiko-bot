"""Value delivery: the signal that separates configured from working.

A feature that was set up and never delivered anything looks identical to a
healthy one in a command-usage dashboard, and they are opposites. Only these
events tell them apart, which is why they are worth their own suite.
"""
import discord
import pytest

from app.constants import Commands as constants
from app.data import analytics as analytics_data
from app.core import analytics, analytics_reports, analytics_sink
from app.data.events import activity
from tests.generators.events import record_event

pytestmark = pytest.mark.unit


def flush():
    analytics.flush()


def test_a_configured_feature_with_no_delivery_is_reported_as_abandoned():
    record_event(
        "setup.completed", guild_id="1", user_id="9", feature="welcome_messages",
        source="slash", session_id="s", duration_ms=1000, steps_viewed=7,
    )
    flush()

    profile = analytics_data.find_profile("1")
    feature = profile["features"]["welcome_messages"]
    assert feature["completed_at"] is not None
    assert feature.get("first_value_at") is None
    assert profile.get("last_value_at") is None


def test_delivery_moves_a_feature_out_of_the_abandoned_set():
    record_event(
        "setup.completed", guild_id="2", user_id="9", feature="default_roles",
        source="slash", session_id="s", duration_ms=1000, steps_viewed=1,
    )
    analytics.record_value("2", "default_roles")
    flush()

    profile = analytics_data.find_profile("2")
    assert profile["features"]["default_roles"]["first_value_at"] is not None
    assert analytics_reports.abandoned_features(days=0) == []


def test_permission_failures_are_counted_separately_from_deliveries():
    analytics.record_value("3", "welcome_messages")
    analytics.record_permission_failure("3", "welcome_messages", discord.Forbidden.__new__(discord.Forbidden))
    flush()

    profile = analytics_data.find_profile("3")
    feature = profile["features"]["welcome_messages"]
    assert feature["value_count"] == 1
    assert feature["permission_failures"] == 1


def test_feature_history_reports_what_a_pause_or_disable_should_carry():
    record_event(
        "setup.completed", guild_id="4", user_id="9", feature="block_links",
        source="slash", session_id="s", duration_ms=1000, steps_viewed=3,
    )
    for _ in range(4):
        record_event(
            "link.blocked", guild_id="4", feature="block_links", host="spam.com",
            mode="block_all", reason="blocked-no-rule", deleted=True,
        )
    flush()

    history = analytics_reports.feature_history("4", "block_links")
    assert history["value_events_since_enabled"] == 4
    assert history["days_since_enabled"] == 0


def test_a_blocked_link_is_one_activity_and_one_value_delivered():
    """Broke as: a blocked link was written twice, a `guild.blocked_links` row and a
    `feature.action_performed` count, and the two disagreed. Guaranteed: one
    `link.blocked` event, one activity document, and the value it counts."""
    for _ in range(20):
        record_event(
            "link.blocked", guild_id="5", feature="block_links", host="spam.com",
            mode="block_all", reason="blocked-no-rule", deleted=True,
        )
    flush()

    assert len(activity.find_by_guild("5")) == 20
    profile = analytics_data.find_profile("5")
    assert profile["features"]["block_links"]["value_count"] == 20


def test_a_failed_delete_is_reported_as_a_permission_problem():
    record_event(
        "value.blocked_by_permission", guild_id="6",
        feature=constants.BLOCK_LINKS_KEY, error_type="Forbidden",
    )
    flush()

    events = activity.find_by_guild("6")
    assert [event["type"] for event in events] == ["value.blocked_by_permission"]
    assert events[0]["props"]["error_type"] == "Forbidden"


def test_the_funnel_reports_activation_not_only_completion():
    record_event(
        "feature.setup_opened", guild_id="7", user_id="9", feature="block_links",
        source="slash", session_id="s1",
    )
    record_event(
        "setup.completed", guild_id="7", user_id="9", feature="block_links",
        source="slash", session_id="s1", duration_ms=1000, steps_viewed=3,
    )
    record_event(
        "feature.setup_opened", guild_id="8", user_id="9", feature="block_links",
        source="slash", session_id="s2",
    )
    record_event(
        "setup.discarded", guild_id="8", user_id="9", feature="block_links",
        session_id="s2",
    )
    analytics.record_value("7", "block_links")
    flush()

    funnel = analytics_reports.funnel("block_links")
    assert funnel["setup_opened"] == 2
    assert funnel["setup_completed"] == 1
    assert funnel["setup_discarded"] == 1
    assert funnel["activated"] == 1


def test_recurring_value_needs_two_distinct_days():
    analytics.record_value("11", "welcome_messages")
    analytics.record_value("11", "welcome_messages")
    flush()

    assert analytics_reports.funnel("welcome_messages")["recurring"] == 0

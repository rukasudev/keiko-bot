"""Value delivery: the signal that separates configured from working.

A feature that was set up and never delivered anything looks identical to a
healthy one in a command-usage dashboard, and they are opposites. Only these
events tell them apart, which is why they are worth their own suite.
"""
import discord
import pytest

from app.constants import Commands as constants
from app.data import analytics as analytics_data
from app.services import analytics, analytics_reports, analytics_sink

pytestmark = pytest.mark.unit


def flush():
    analytics.flush()


def test_a_configured_feature_with_no_delivery_is_reported_as_abandoned():
    analytics.emit(
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
    analytics.emit(
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
    analytics.emit(
        "setup.completed", guild_id="4", user_id="9", feature="block_links",
        source="slash", session_id="s", duration_ms=1000, steps_viewed=3,
    )
    for _ in range(4):
        analytics.emit(
            "feature.action_performed", guild_id="4", feature="block_links",
            mode="block_all", reason="blocked-no-rule", deleted=True,
        )
    flush()

    history = analytics_reports.feature_history("4", "block_links")
    assert history["value_events_since_enabled"] == 4
    assert history["days_since_enabled"] == 0


def test_moderation_actions_never_create_documents_at_volume():
    for _ in range(200):
        analytics.emit(
            "feature.action_performed", guild_id="5", feature="block_links",
            mode="block_all", reason="blocked-no-rule", deleted=True,
        )
    flush()

    assert analytics_data.find_events_by_guild("5") == []
    profile = analytics_data.find_profile("5")
    assert profile["features"]["block_links"]["value_count"] == 200


def test_a_failed_delete_is_reported_as_a_permission_problem():
    analytics.emit(
        "value.blocked_by_permission", guild_id="6",
        feature=constants.BLOCK_LINKS_KEY, error_type="Forbidden",
    )
    flush()

    events = analytics_data.find_events_by_guild("6")
    assert [event["event"] for event in events] == ["value.blocked_by_permission"]
    assert events[0]["props"]["error_type"] == "Forbidden"


def test_the_funnel_reports_activation_not_only_completion():
    analytics.emit(
        "feature.setup_opened", guild_id="7", user_id="9", feature="block_links",
        source="slash", session_id="s1",
    )
    analytics.emit(
        "setup.completed", guild_id="7", user_id="9", feature="block_links",
        source="slash", session_id="s1", duration_ms=1000, steps_viewed=3,
    )
    analytics.emit(
        "feature.setup_opened", guild_id="8", user_id="9", feature="block_links",
        source="slash", session_id="s2",
    )
    analytics.emit(
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

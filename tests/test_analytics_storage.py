"""Storage contract: what becomes a document, what becomes a counter.

The whole storage budget rests on high-volume events never being written one
document at a time, and on every derived number the dashboards read living in
the guild profile so no dashboard has to scan raw events.
"""
import pytest

from app.constants import Commands as constants
from app.data import analytics as analytics_data
from app.services import analytics, analytics_sink
from app.services.trace import trace_scope

pytestmark = pytest.mark.unit


def _flush(*emissions):
    for event, props in emissions:
        analytics.emit(event, **props)
    analytics.flush()


def test_low_volume_events_are_stored_as_documents():
    _flush(("setup.completed", {
        "guild_id": "10", "user_id": "1", "feature": "block_links",
        "source": "slash", "session_id": "s1", "duration_ms": 1000,
        "steps_viewed": 4,
    }))

    events = analytics_data.find_events_by_guild("10")
    assert len(events) == 1
    assert events[0]["event"] == "setup.completed"
    assert events[0]["props"]["steps_viewed"] == 4


def test_value_delivery_never_creates_a_document():
    for _ in range(50):
        analytics.emit("value.delivered", guild_id="20", feature="welcome_messages")
    analytics.flush()

    assert analytics_data.find_events_by_guild("20") == []

    buckets = analytics_data.find_month_buckets("20")
    assert len(buckets) == 1
    day = list(buckets[0]["days"].values())[0]
    assert day["value_delivered|welcome_messages"] == 50


def test_delivery_advances_the_guild_profile_counters():
    _flush(
        ("value.delivered", {"guild_id": "30", "feature": "default_roles"}),
        ("value.delivered", {"guild_id": "30", "feature": "default_roles"}),
        ("value.blocked_by_permission", {
            "guild_id": "30", "feature": "default_roles", "error_type": "Forbidden",
        }),
    )

    profile = analytics_data.find_profile("30")
    assert profile["features"]["default_roles"]["value_count"] == 2
    assert profile["features"]["default_roles"]["permission_failures"] == 1
    assert profile["totals"]["value_events"] == 2
    assert profile["features"]["default_roles"]["first_value_at"] is not None
    assert profile["last_value_at"] is not None


def test_profile_tracks_the_setup_funnel_per_feature():
    _flush(
        ("feature.setup_opened", {
            "guild_id": "40", "feature": "welcome_messages",
            "source": "greeting_button", "session_id": "s1",
        }),
        ("setup.validation_failed", {
            "guild_id": "40", "feature": "welcome_messages", "session_id": "s1",
            "step_key": "message", "validation": "validate_length",
            "error_key": "too-long",
        }),
        ("setup.completed", {
            "guild_id": "40", "user_id": "77", "feature": "welcome_messages",
            "source": "greeting_button", "session_id": "s1",
            "duration_ms": 60000, "steps_viewed": 7,
        }),
    )

    profile = analytics_data.find_profile("40")
    feature = profile["features"]["welcome_messages"]
    assert feature["setup_attempts"] == 1
    assert feature["validation_failures"] == 1
    assert feature["completed_at"] is not None
    assert profile["totals"]["setups_completed"] == 1
    assert profile["admins"] == ["77"]


def test_removal_stamps_the_profile_so_retention_can_expire_it():
    _flush(
        ("guild.joined", {"guild_id": "50", "returning": False}),
        ("guild.removed", {"guild_id": "50", "days_in_guild": 12}),
    )

    profile = analytics_data.find_profile("50")
    assert profile["joined_at"] is not None
    assert profile["removed_at"] is not None


def test_rejoining_clears_the_removal_stamp():
    _flush(("guild.removed", {"guild_id": "60", "days_in_guild": 3}))
    _flush(("guild.joined", {"guild_id": "60", "returning": True}))

    assert analytics_data.find_profile("60")["removed_at"] is None


def test_every_redis_analytics_counter_expires(redis_client):
    _flush(("value.delivered", {"guild_id": "70", "feature": "block_links"}))

    keys = [key for key in redis_client.keys("guild:70:analytics:*")]
    assert keys, "the hot window should have been written"
    for key in keys:
        assert redis_client.ttl(key) == constants.ANALYTICS_HOT_WINDOW_SECONDS


def test_metric_keys_drop_the_dots_mongo_would_read_as_nesting():
    assert analytics_sink.metric_key("value.delivered", "block_links") == (
        "value_delivered|block_links"
    )
    assert analytics_sink.metric_key("guild.joined", None) == "guild_joined"


def test_events_without_a_guild_never_reach_the_per_guild_stores():
    _flush(("command.failed", {"command": "ping", "error_type": "TimeoutError"}))

    assert analytics_data.find_profiles() == []
    assert analytics_data.find_month_buckets("") == []


def test_the_trace_supplies_context_the_call_site_does_not_repeat():
    with trace_scope("cmd", guild_id="80", user_id="9", source="slash",
                     feature="block_links", session_id="sess"):
        analytics.emit("feature.setup_opened")
    analytics.flush()

    event = analytics_data.find_events_by_guild("80")[0]
    assert event["user_id"] == "9"
    assert event["source"] == "slash"
    assert event["feature"] == "block_links"
    assert event["session_id"] == "sess"

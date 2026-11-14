"""Storage contract: which collection an event lands in, and what is only counted.

The whole storage budget rests on high-volume events never being written one
document at a time, and on every derived number the dashboards read living in
the guild profile so no dashboard has to scan raw events.
"""
import contextlib

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.constants import Commands as constants
from app.data import analytics as analytics_data
from app.core import analytics, analytics_sink
from app.core.trace import trace_scope
from app.data.events import activity, changes, interactions
from tests.generators.events import record_event, stored

pytestmark = pytest.mark.unit


def _flush(*emissions):
    for event, props in emissions:
        record_event(event, **props)
    analytics.flush()


def test_an_interaction_is_written_in_the_sinks_batch():
    _flush(("setup.completed", {
        "guild_id": "10", "user_id": "1", "feature": "block_links",
        "source": "slash", "session_id": "s1", "duration_ms": 1000,
        "steps_viewed": 4,
    }))

    events = interactions.find_by_guild("10")
    assert len(events) == 1
    assert events[0]["type"] == "setup.completed"
    assert events[0]["props"]["steps_viewed"] == 4
    assert stored("10") == events


def test_a_change_is_written_at_once_and_counted_by_the_sink():
    """A change is the history a panel lists: it is in the database as soon as
    `record` returns, before any flush, and the sink still counts it."""
    record_event("feature.paused", guild_id="11", feature="block_links", source="manager")

    [change] = changes.find_by_guild("11")
    assert change["type"] == "feature.paused"
    assert change["cog"] == "block_links"

    analytics.flush()
    assert stored("11") == [change], "the sink wrote the change a second time"
    day = list(analytics_data.find_month_buckets("11")[0]["days"].values())[0]
    assert day["feature_paused|block_links"] == 1


def test_activity_lands_in_its_own_collection():
    _flush(("value.blocked_by_permission", {
        "guild_id": "12", "feature": "welcome_messages", "error_type": "Forbidden",
    }))

    [event] = activity.find_by_guild("12")
    assert event["type"] == "value.blocked_by_permission"
    assert event["actor"] == {"kind": "bot", "id": None}
    assert interactions.find_by_guild("12") == []


def test_value_delivery_never_creates_a_document():
    for _ in range(50):
        analytics.record_value("20", "welcome_messages")
    analytics.flush()

    assert stored("20") == []

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


def test_only_a_greeting_posted_on_a_channel_stamps_the_profile():
    """Broke as: `PROFILE_RULES` stamped `greeting_sent_at` for every
    `guild.greeting_sent`, so a server with no writable channel read as greeted in
    the reports. Shared behaviour: the guild profile every dashboard reads, and the
    `greeting_sent` of `analytics_reports`. Guaranteed: the event is stored as ever,
    and only a greeting posted on a channel stamps the profile."""
    _flush(
        ("guild.greeting_sent", {
            "guild_id": "65", "features_offered": 7, "channel_found": False,
        }),
        ("guild.greeting_sent", {
            "guild_id": "66", "features_offered": 7, "channel_found": True,
        }),
    )

    unposted = analytics_data.find_profile("65") or {}
    assert unposted.get("greeting_sent_at") is None, (
        "a greeting with no channel to post on was stamped as sent"
    )
    assert analytics_data.find_profile("66")["greeting_sent_at"] is not None
    assert [
        event["props"]["channel_found"] for event in activity.find_by_guild("65")
    ] == [False]


def test_every_redis_analytics_counter_expires(redis_client):
    _flush(("value.delivered", {"guild_id": "70", "feature": "block_links"}))

    keys = [key for key in redis_client.keys("guild:70:analytics:*")]
    assert keys, "the hot window should have been written"
    for key in keys:
        assert redis_client.ttl(key) == constants.ANALYTICS_HOT_WINDOW_SECONDS


class _DropsAfterOneRoundTrip:
    """A Redis whose connection drops once it has answered one round trip."""

    def __init__(self, inner):
        self.inner = inner
        self.trips = 0

    def _trip(self):
        self.trips += 1
        if self.trips > 1:
            raise RedisConnectionError("Connection reset by peer")

    def incrby(self, *args, **kwargs):
        self._trip()
        return self.inner.incrby(*args, **kwargs)

    def expire(self, *args, **kwargs):
        self._trip()
        return self.inner.expire(*args, **kwargs)

    def pipeline(self, transaction=True):
        pipeline = self.inner.pipeline(transaction)
        execute = pipeline.execute

        def one_round_trip():
            self._trip()
            return execute()

        pipeline.execute = one_round_trip
        return pipeline


def test_a_counter_never_outlives_its_window_when_redis_drops_midway(
    redis_client, monkeypatch
):
    """Broke as: `increment_redis_key_with_expiration` sent INCRBY and EXPIRE in two
    round trips, so a connection lost between them left an analytics counter with no
    TTL, kept forever. Shared behaviour: every expiring counter (the attempt count of
    `analytics.count_attempt`, the hot window of `analytics_sink`). Guaranteed: a
    counter is written with its TTL in one round trip, or not at all."""
    from app.core import cache

    monkeypatch.setattr(cache, "redis_client", _DropsAfterOneRoundTrip(redis_client))

    with contextlib.suppress(RedisConnectionError):
        cache.increment_redis_key_with_expiration("guild:80:analytics:probe", 1, 600)

    assert redis_client.get("guild:80:analytics:probe") == "1"
    assert redis_client.ttl("guild:80:analytics:probe") == 600, (
        "the counter was written without its TTL"
    )


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
        analytics.record("feature.setup_opened")
    analytics.flush()

    event = interactions.find_by_guild("80")[0]
    assert event["actor"] == {"kind": "user", "id": "9"}
    assert event["props"]["source"] == "slash"
    assert event["cog"] == "block_links"
    assert event["session_id"] == "sess"
    assert event["trace_id"]


def test_every_event_has_the_one_envelope():
    """The envelope of database PR 2: what happened, where, who, the trace and the
    session, the props, the version; no id, environment or release of its own (the
    trace and the deploy tag answer those)."""
    _flush(("command.failed", {
        "guild_id": "90", "user_id": "9", "feature": "block_links",
        "command": "ping", "error_type": "KeyError",
    }))

    [event] = interactions.find_by_guild("90")
    assert set(event) == {
        "ts", "type", "guild_id", "cog", "actor", "trace_id", "session_id", "props", "v",
    }
    assert event["props"] == {"command": "ping", "error_type": "KeyError"}
    assert event["v"] == 1

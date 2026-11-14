"""Storage contract: which collection an event lands in, and what is only counted.

The whole storage budget rests on high-volume events never being written one
document at a time, and on every derived number the dashboards read living in
the guild profile so no dashboard has to scan raw events.
"""
import contextlib
from datetime import datetime, timezone

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.data import data, wait
from app.constants import Commands as constants
from app.core import analytics, analytics_sink
from app.core.trace import trace_scope
from tests.generators.events import record_event, stored

pytestmark = pytest.mark.unit


def _flush(*emissions):
    for event, props in emissions:
        record_event(event, **props)
    wait(analytics.flush())


def test_an_interaction_is_written_in_the_sinks_batch():
    _flush(("setup.completed", {
        "guild_id": "10", "user_id": "1", "feature": "block_links",
        "source": "slash", "session_id": "s1", "duration_ms": 1000,
        "steps_viewed": 4,
    }))

    events = wait(data().events_interactions.find_by_guild("10"))
    assert len(events) == 1
    assert events[0]["type"] == "setup.completed"
    assert events[0]["props"]["steps_viewed"] == 4
    assert stored("10") == events


def test_a_change_is_written_at_once_and_counted_by_the_sink():
    """A change is the history a panel lists: it is in the database as soon as
    `record` returns, before any flush, and the sink still counts it."""
    record_event("feature.paused", guild_id="11", feature="block_links", source="manager")

    [change] = wait(data().events_changes.find_by_guild("11"))
    assert change["type"] == "feature.paused"
    assert change["cog"] == "block_links"

    wait(analytics.flush())
    assert stored("11") == [change], "the sink wrote the change a second time"
    day = list(wait(data().analytics_counts.find_by_guild("11"))[0]["days"].values())[0]
    assert day["feature_paused|block_links"] == 1


def test_activity_lands_in_its_own_collection():
    _flush(("value.blocked_by_permission", {
        "guild_id": "12", "feature": "welcome_messages", "error_type": "Forbidden",
    }))

    [event] = wait(data().events_activity.find_by_guild("12"))
    assert event["type"] == "value.blocked_by_permission"
    assert event["actor"] == {"kind": "bot", "id": None}
    assert wait(data().events_interactions.find_by_guild("12")) == []


def test_value_delivery_never_creates_a_document():
    for _ in range(50):
        analytics.record_value("20", "welcome_messages")
    wait(analytics.flush())

    assert stored("20") == []

    buckets = wait(data().analytics_counts.find_by_guild("20"))
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

    profile = wait(data().analytics_lifetime.find_lifetime("30"))
    assert profile["features"]["default_roles"]["value_count"] == 2
    assert profile["features"]["default_roles"]["permission_failures"] == 1
    assert profile["totals"]["value_events"] == 2
    assert profile["features"]["default_roles"]["first_value_at"] is not None
    assert profile["last_value_at"] is not None


def test_one_batch_keeps_the_first_and_the_last_value_in_time_whatever_their_order():
    """Broke as: the sink folded every event of a batch into one update by keeping the
    last event's timestamp, so `$min` and `$max` got the batch's last moment, and a
    server's first value read as a later one and its last value as an earlier one.

    Guaranteed: within one batch, `first_value_at` keeps the earliest moment and
    `last_value_at` the latest, in whatever order the events were queued."""
    first, last, between = (
        datetime(2026, 11, 1, hour, tzinfo=timezone.utc) for hour in (9, 11, 10)
    )
    for moment in (first, last, between):
        record_event("value.delivered", guild_id="35", feature="default_roles")["ts"] = moment
    wait(analytics.flush())

    profile = wait(data().analytics_lifetime.find_lifetime("35"))
    assert profile["features"]["default_roles"]["first_value_at"] == first
    assert profile["features"]["default_roles"]["last_value_at"] == last
    assert profile["last_value_at"] == last


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

    profile = wait(data().analytics_lifetime.find_lifetime("40"))
    feature = profile["features"]["welcome_messages"]
    assert feature["setup_attempts"] == 1
    assert feature["validation_failures"] == 1
    assert feature["completed_at"] is not None
    assert profile["totals"]["setups_completed"] == 1
    assert profile["admins"] == ["77"]
    assert profile["admins_count"] == 1


def test_removal_stamps_the_profile_so_retention_can_expire_it():
    _flush(
        ("guild.joined", {"guild_id": "50", "returning": False}),
        ("guild.removed", {"guild_id": "50", "days_in_guild": 12}),
    )

    profile = wait(data().analytics_lifetime.find_lifetime("50"))
    assert profile["joined_at"] is not None
    assert profile["removed_at"] is not None


def test_rejoining_clears_the_removal_stamp():
    _flush(("guild.removed", {"guild_id": "60", "days_in_guild": 3}))
    _flush(("guild.joined", {"guild_id": "60", "returning": True}))

    assert wait(data().analytics_lifetime.find_lifetime("60"))["removed_at"] is None


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

    unposted = wait(data().analytics_lifetime.find_lifetime("65")) or {}
    assert unposted.get("greeting_sent_at") is None, (
        "a greeting with no channel to post on was stamped as sent"
    )
    assert wait(data().analytics_lifetime.find_lifetime("66"))["greeting_sent_at"] is not None
    assert [
        event["props"]["channel_found"] for event in wait(data().events_activity.find_by_guild("65"))
    ] == [False]


def test_the_sink_writes_no_counter_to_redis(redis_client):
    _flush(("value.delivered", {"guild_id": "70", "feature": "block_links"}))

    assert redis_client.keys("guild:70:analytics:*") == [], (
        "the daily window nobody read is written again"
    )
    assert wait(data().analytics_counts.find_by_guild("70")), "the count went to Mongo"


def _counts(guild_id):
    [document] = wait(data().analytics_counts.find_by_guild(guild_id))
    return document


def test_every_event_with_a_guild_is_counted_by_day_and_a_command_by_name():
    _flush(
        ("command.invoked", {
            "guild_id": "71", "command": "admin overview", "source": "slash",
            "feature": "block_links",
        }),
        ("command.invoked", {"guild_id": "71", "command": "help", "source": "slash"}),
        ("setup.discarded", {
            "guild_id": "71", "feature": "welcome_messages", "session_id": "s1",
        }),
    )

    document = _counts("71")
    [day] = document["days"].values()
    assert document["_id"] == f"71|{document['month']}"
    assert day == {
        "command_invoked|block_links": 1,
        "command_invoked|block_links|admin overview": 1,
        "command_invoked": 1,
        "command_invoked||help": 1,
        "setup_discarded|welcome_messages": 1,
    }


def _keiko(counter_id):
    [document] = wait(data().analytics_keiko.find_counters([counter_id]))
    return document


def test_keiko_sums_every_server_by_day_and_counts_each_command():
    _flush(
        ("value.delivered", {"guild_id": "72", "feature": "welcome_messages"}),
        ("value.delivered", {"guild_id": "73", "feature": "welcome_messages"}),
        ("command.invoked", {"guild_id": "72", "command": "birthday", "source": "slash"}),
        ("command.failed", {
            "guild_id": "73", "command": "birthday", "error_type": "KeyError",
        }),
        ("command.failed", {"command": "birthday", "error_type": "Forbidden"}),
    )
    month = analytics_sink.now().strftime("%Y-%m")
    day = analytics_sink.now().strftime("%d")

    daily = _keiko(f"day:{month}")
    assert daily["month"] == month
    assert daily["days"][day]["value_delivered|welcome_messages"] == 2
    assert daily["days"][day]["command_failed"] == 2
    assert _keiko(f"commands:{month}")["days"][day] == {
        "birthday": {
            "invoked": 1,
            "failed": 2,
            "errors": {"KeyError": 1, "Forbidden": 1},
        }
    }


def test_a_blocked_link_adds_to_keikos_total_and_its_sites_naming_no_one():
    """Broke as: block links counted Keiko's own total itself, in
    `guild.block_links_totals`, beside the event it emitted. Guaranteed: the sink
    derives `blocked:total` and `blocked:site:{host}` from `link.blocked`, and those
    documents name no server and no member."""
    batch = [
        {
            "type": "link.blocked", "ts": analytics_sink.now(), "guild_id": guild,
            "actor": {"kind": "user", "id": "111"}, "cog": "block_links",
            "props": {"host": host},
        }
        for guild, host in (("74", "spam-site.com"), ("75", "spam-site.com"), ("74", "x.io"))
    ]

    wait(analytics_sink.persist(batch))

    documents = {
        document["_id"]: {
            field: value for field, value in document.items() if field != "updated_at"
        }
        for document in wait(data().analytics_keiko.find_counters(
            ["blocked:total", "blocked:site:spam-site.com", "blocked:site:x.io"]
        ))
    }
    assert documents == {
        "blocked:total": {"_id": "blocked:total", "blocked": 3},
        "blocked:site:spam-site.com": {
            "_id": "blocked:site:spam-site.com", "host": "spam-site.com", "blocked": 2,
        },
        "blocked:site:x.io": {"_id": "blocked:site:x.io", "host": "x.io", "blocked": 1},
    }


def test_a_commands_latency_lands_in_its_bucket_and_a_slow_run_is_kept(deps):
    started = analytics_sink.now()
    closed = [
        {"command": "birthday", "ts": started, "duration_ms": 120, "run": None},
        {"command": "birthday", "ts": started, "duration_ms": 2600, "run": {
            "trace_id": "abc123", "ts": started, "name": "birthday", "duration_ms": 2600,
        }},
        {"command": None, "ts": started, "duration_ms": 9000, "run": {
            "trace_id": "def456", "ts": started, "name": "twitch webhook",
        }},
    ]

    wait(analytics_sink.persist_runs(closed))

    month, day = started.strftime("%Y-%m"), started.strftime("%d")
    assert _keiko(f"commands:{month}")["days"][day]["birthday"]["ms"] == {
        "0-250": 1, "2000-5000": 1,
    }
    kept = deps.mongo_client.traces.runs.find({})
    assert sorted(run["trace_id"] for run in kept) == ["abc123", "def456"]


def test_the_latency_buckets_follow_the_edges_the_constants_name():
    edges = constants.COMMAND_LATENCY_EDGES_MS

    assert analytics_sink.latency_bucket(0) == f"0-{edges[0]}"
    assert analytics_sink.latency_bucket(edges[0]) == f"{edges[0]}-{edges[1]}"
    assert analytics_sink.latency_bucket(edges[-1] * 10) == f"{edges[-1]}+"


def test_a_servers_admins_are_counted_once_each_up_to_the_cap(monkeypatch):
    monkeypatch.setattr(constants, "ANALYTICS_LIFETIME_ADMINS_MAX", 2)
    for user in ("1", "1", "2", "3"):
        _flush(("config.changed", {
            "guild_id": "76", "user_id": user, "feature": "block_links",
            "changed_keys": ["mode"],
        }))

    found = wait(data().analytics_lifetime.find_lifetime("76"))
    assert found["admins_count"] == 2
    assert sorted(found["admins"]) == ["1", "2"]


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
        cache.increment_redis_key_with_expiration("window:probe:80", 1, 600)

    assert redis_client.get("window:probe:80") == "1"
    assert redis_client.ttl("window:probe:80") == 600, (
        "the counter was written without its TTL"
    )


def test_metric_keys_drop_the_dots_mongo_would_read_as_nesting():
    assert analytics_sink.metric_key("value.delivered", "block_links") == (
        "value_delivered|block_links"
    )
    assert analytics_sink.metric_key("guild.joined", None) == "guild_joined"


def test_events_without_a_guild_never_reach_the_per_guild_stores():
    _flush(("command.failed", {"command": "ping", "error_type": "TimeoutError"}))

    assert wait(data().analytics_lifetime.find_lifetimes()) == []
    assert wait(data().analytics_counts.find_by_guild("")) == []


def test_the_trace_supplies_context_the_call_site_does_not_repeat():
    with trace_scope("cmd", guild_id="80", user_id="9", source="slash",
                     feature="block_links", session_id="sess"):
        analytics.record("feature.setup_opened")
    wait(analytics.flush())

    event = wait(data().events_interactions.find_by_guild("80"))[0]
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

    [event] = wait(data().events_interactions.find_by_guild("90"))
    assert set(event) == {
        "ts", "type", "guild_id", "cog", "actor", "trace_id", "session_id", "props", "v",
    }
    assert event["props"] == {"command": "ping", "error_type": "KeyError"}
    assert event["v"] == 1

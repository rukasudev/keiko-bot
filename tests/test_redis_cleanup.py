"""`python -m app.migrations redis-cleanup` and `redis-check`: the release's Redis step.

The keys of the retired families (counters without a TTL, the block links hash, the
analytics daily window, v0.9.0's settings cache and the old names of the renamed claims,
caches and throttles) are unlinked once; afterwards every key carries one of the four
role prefixes and a TTL. The check fails the release on a key without a TTL, and lists
as a warning a key outside the four roles that has one: v0.9.0 cached translations
keyed by their raw text for a day, and those expire on their own.
"""

import io

import pytest

from app.constants import Commands, Migrations
from app.migrations.__main__ import main
from tests.mocks.database import MockRedisClient

pytestmark = pytest.mark.unit

RETIRED = {
    "command.call:block_links": None,
    "command.call:block_links:button": None,
    "command.error:notifications_twitch": None,
    "guild:80:block_links:counters": 400 * 86400,
    "guild:80:block_links:total": None,
    "guild:80:block_links:host:example.com": None,
    "guild:80:analytics:2026-11-12:value_delivered|block_links": 8 * 86400,
    "guild:80:analytics:attempts:block_links": 86400,
    "guild:80:cog.block_links": 30 * 86400,
    "twitch:stream:gaules:2026-11-12T20:00:00Z:notified": 86400,
    "youtube:video:video-1:notified": 7 * 86400,
    "stream_elements:channel:se-gaules:commands": 86400,
    "stream_elements:streamer:gaules:warned": 1800,
    "streamelements:commands:se-gaules": 86400,
}

KEPT = {
    "cache:cog:80:block_links": 300,
    Commands.REDIS_TWITCH_LIVE_CLAIM.format(
        streamer="gaules", started_at="2026-11-12T20:00:00Z"
    ): 86400,
    Commands.REDIS_YOUTUBE_VIDEO_CLAIM.format(video_id="video-1"): 7 * 86400,
    Commands.CACHE_STREAM_ELEMENTS_COMMANDS.format(channel_id="se-gaules"): 86400,
    Commands.REDIS_STREAM_ELEMENTS_CHANNEL_THROTTLE.format(streamer="gaules"): 300,
    Commands.REDIS_STREAM_ELEMENTS_WARNED_THROTTLE.format(streamer="gaules"): 1800,
    Commands.REDIS_ATTEMPTS_WINDOW.format(guild_id="80", feature="block_links"): 86400,
}


def _seeded(keys):
    redis = MockRedisClient()
    for key, seconds in keys.items():
        redis.set(key, "1", ex=seconds)
    return redis


def _run(redis, *argv):
    out = io.StringIO()
    code = main(list(argv), out=out, redis_client=redis)
    return code, out.getvalue()


def test_every_renamed_key_carries_a_role_prefix():
    for key in KEPT:
        assert key.startswith(Migrations.REDIS_ROLE_PREFIXES), key


def test_the_cleanup_unlinks_every_retired_family_and_keeps_the_roles():
    redis = _seeded({**RETIRED, **KEPT})

    code, out = _run(redis, "redis-cleanup")

    assert code == 0, out
    assert sorted(redis.keys("*")) == sorted(KEPT)
    assert "command.call:*: unlinked 2 key(s)" in out
    assert "guild:*:block_links:*: unlinked 3 key(s)" in out


def test_a_dry_run_counts_and_unlinks_nothing():
    redis = _seeded({**RETIRED, **KEPT})

    code, out = _run(redis, "redis-cleanup", "--dry-run")

    assert code == 0, out
    assert sorted(redis.keys("*")) == sorted({**RETIRED, **KEPT})
    assert "stream_elements:*: would unlink 2 key(s)" in out


def test_the_cleanup_run_twice_finds_nothing_the_second_time():
    redis = _seeded({**RETIRED, **KEPT})
    _run(redis, "redis-cleanup")

    code, out = _run(redis, "redis-cleanup")

    assert code == 0, out
    assert sorted(redis.keys("*")) == sorted(KEPT)
    assert out.count("unlinked 0 key(s)") == len(Migrations.REDIS_RETIRED_PATTERNS)


def test_the_cleanup_unlinks_more_keys_than_one_scan_batch(monkeypatch):
    monkeypatch.setattr(Migrations, "REDIS_SCAN_BATCH", 2)
    redis = _seeded({f"command.call:feature_{index}": None for index in range(5)})

    code, out = _run(redis, "redis-cleanup")

    assert code == 0, out
    assert redis.keys("*") == []
    assert "command.call:*: unlinked 5 key(s)" in out


def test_the_check_passes_once_every_key_has_a_role_and_a_ttl():
    redis = _seeded({**RETIRED, **KEPT})
    _run(redis, "redis-cleanup")

    code, out = _run(redis, "redis-check")

    assert code == 0, out
    assert "fail" not in out


def test_the_check_fails_on_a_key_without_a_ttl_whatever_its_name():
    redis = _seeded({**KEPT, "cache:forever": None, "leftover:80": None})

    code, out = _run(redis, "redis-check")

    assert code == 1
    assert "fail    2 key(s) without a TTL: cache:forever, leftover:80" in out


def test_the_check_only_warns_on_a_key_outside_the_roles_that_expires_on_its_own():
    """Broke as: the check failed the release on every key outside the four prefixes,
    and v0.9.0 left translations cached under their raw text with a day's TTL, so the
    first release after it would stop on keys that were already going away.
    Guaranteed: such a key is listed as a warning and the step still passes."""
    redis = _seeded({**KEPT, "Bom dia a todos": 86400})

    code, out = _run(redis, "redis-check")

    assert code == 0, out
    assert (
        "warn    1 key(s) with no role prefix (cache, claim, throttle, window), "
        "left to expire: Bom dia a todos"
    ) in out
    assert "fail" not in out


def test_the_check_fails_on_the_retired_families_left_in_place():
    code, out = _run(_seeded({**RETIRED, **KEPT}), "redis-check")
    lasting = [key for key, seconds in RETIRED.items() if seconds is None]

    assert code == 1
    assert f"{len(lasting)} key(s) without a TTL" in out
    assert f"{len(RETIRED) - len(lasting)} key(s) with no role prefix" in out


def test_without_a_redis_url_the_step_fails_and_says_so(monkeypatch):
    monkeypatch.setattr("app.migrations.__main__.redis_url", lambda: "")

    code, out = _run(None, "redis-check")

    assert code == 1
    assert "no Redis URL" in out
